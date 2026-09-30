from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

from tests.fixtures.developer import fake_workspace


pytestmark = pytest.mark.subsystem


def test_command_dataclasses_and_split_preserve_quoted_arguments() -> None:
    from row_bot.developer.runtime import CommandResult, CommandSpec, split_command

    spec = CommandSpec("Run tests", "python -m pytest", "test")
    result = CommandResult(command=spec.command, cwd=".", returncode=0, stdout="ok")

    assert spec.label == "Run tests"
    assert result.ran is True
    assert result.ok is True
    assert split_command('python -c "print(\\"hello world\\")" --flag=value') == [
        "python",
        "-c",
        'print("hello world")',
        "--flag=value",
    ]


def test_shell_control_operators_ignore_quoted_text_but_detect_unquoted() -> None:
    from row_bot.developer import runtime

    assert runtime._unquoted_text('python -c "print(1 | 2)"') == "python -c "
    assert runtime.has_shell_control_operator('python -c "print(1 | 2)"') is False
    assert runtime.has_shell_control_operator('python -m pytest && git status') is True
    assert runtime.has_shell_control_operator("echo hi > out.txt") is True


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("python -m pip install rich", "run_install"),
        ("uv add rich", "run_install"),
        ("curl https://example.com", "run_network"),
        ("python -c \"import requests; requests.post('https://example.com')\"", "run_network"),
        ("rm -rf build", "delete"),
        ("Remove-Item build -Recurse", "delete"),
        ("git commit -m test", "git_commit"),
        ("git push origin HEAD", "git_push"),
        ("gh pr create --draft", "git_pr"),
        ("docker run alpine", "run_network"),
        ("python -m pytest", "run_safe_command"),
        ("unknown-tool --version", "run_safe_command"),
    ],
)
def test_command_classification_covers_risky_and_safe_actions(command: str, expected: str) -> None:
    from row_bot.developer.runtime import classify_command_action

    assert classify_command_action(command) == expected


def test_detect_project_commands_and_js_runner_order(tmp_path) -> None:
    from row_bot.developer import runtime

    workspace = fake_workspace(tmp_path)
    root = pathlib.Path(workspace.path)
    (root / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest", "lint": "eslint .", "typecheck": "tsc", "dev": "vite"}}),
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (root / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")
    (root / "yarn.lock").write_text("# yarn\n", encoding="utf-8")

    commands = runtime.detect_project_commands(workspace.path)

    assert runtime._detect_js_runner(root) == "pnpm"
    assert [command.command for command in commands] == [
        "pnpm test",
        "pnpm run lint",
        "pnpm run typecheck",
        "pnpm run dev",
        "python -m pytest",
    ]


def test_detect_project_commands_handles_missing_and_invalid_package_json(tmp_path) -> None:
    from row_bot.developer import runtime

    empty = fake_workspace(tmp_path / "empty")
    assert runtime.detect_project_commands(empty.path) == []

    invalid = fake_workspace(tmp_path / "invalid")
    root = pathlib.Path(invalid.path)
    (root / "package.json").write_text("{bad-json", encoding="utf-8")
    (root / "manage.py").write_text("# django", encoding="utf-8")

    assert runtime.detect_project_commands(invalid.path) == [
        runtime.CommandSpec("Django tests", "python manage.py test")
    ]


def test_platform_shell_args_match_host_shell() -> None:
    from row_bot.developer import runtime

    args = runtime._platform_shell_args("echo hi")

    if os.name == "nt":
        assert args[:4] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"]
        assert args[-1] == "echo hi"
    else:
        assert args == ["/bin/sh", "-lc", "echo hi"]


def test_git_status_head_and_snapshot_helpers(monkeypatch, tmp_path) -> None:
    from row_bot.developer import runtime

    root = tmp_path / "repo"
    root.mkdir()
    (root / "text.txt").write_text("hello", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"\x00\x01")

    def fake_run(argv, **_kwargs):
        if argv[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(argv, 0, stdout=" M text.txt\n?? binary.bin\n D gone.txt\nR  old.txt -> new.txt\n")
        if argv[:2] == ["git", "show"]:
            return subprocess.CompletedProcess(argv, 0, stdout="old text")
        return subprocess.CompletedProcess(argv, 1, stdout="")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)

    assert runtime._git_status_paths(root) == {"text.txt", "binary.bin", "gone.txt", "old.txt", "new.txt"}
    assert runtime._head_text(root, "text.txt") == "old text"
    assert runtime._looks_text(root / "text.txt") is True
    assert runtime._looks_text(root / "binary.bin") is False

    snapshot = runtime._snapshot_changed_files(root)
    assert snapshot["text.txt"] == "hello"
    assert snapshot["binary.bin"] is None
    assert snapshot["gone.txt"] is None


@pytest.fixture
def isolated_runtime(tmp_path, reload_for_data_dir):
    """The runtime with its workspace store and change ledger in this test's folder."""
    _storage, _ledger, runtime = reload_for_data_dir(
        tmp_path / "data",
        "row_bot.developer.storage",
        "row_bot.developer.change_ledger",
        "row_bot.developer.runtime",
    )
    return runtime


def test_package_manager_scripts_are_detected_with_dev_servers_as_servers(tmp_path, isolated_runtime) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")
    (repo / "package.json").write_text(
        '{"scripts": {"dev": "vite", "start": "vite --host", "test": "vitest"}}',
        encoding="utf-8",
    )

    by_command = {spec.command: spec for spec in isolated_runtime.detect_project_commands(str(repo))}

    assert "pnpm test" in by_command
    assert by_command["pnpm run dev"].kind == "server"
    assert by_command["pnpm run start"].kind == "server"


def test_block_mode_never_runs_an_install(tmp_path, isolated_runtime) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = isolated_runtime.run_workspace_command(str(repo), "python -m pip install sampleproject", "block")

    assert result.ran is False
    assert result.decision.decision == "block"


def test_chained_shell_commands_need_approval(tmp_path, isolated_runtime) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = isolated_runtime.run_workspace_command(str(repo), "python --version && python --version", "approve")

    assert result.ran is False
    assert result.decision.decision == "ask"


def test_an_approved_shell_command_records_the_files_it_changed(tmp_path, monkeypatch, isolated_runtime) -> None:
    from row_bot.developer import change_ledger, storage
    from row_bot.developer.state import DeveloperWorkspace

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True, text=True)
    workspace = DeveloperWorkspace(id="ws-1", name="repo", path=str(repo))
    monkeypatch.setattr(storage, "get_workspace",
                        lambda workspace_id: workspace if workspace_id == workspace.id else None)
    command = (
        "python -c \"from pathlib import Path; "
        "Path('created.txt').write_text('hello\\n', encoding='utf-8'); print('httpx. marker')\""
    )

    blocked = isolated_runtime.run_workspace_shell_command(
        str(repo), command, "approve", workspace_id="ws-1", thread_id="thread-1")
    assert blocked.ran is False
    assert blocked.decision.decision == "ask"

    result = isolated_runtime.run_workspace_shell_command(
        str(repo), command, "approve", workspace_id="ws-1", thread_id="thread-1", confirmed=True)

    assert result.ran is True
    assert result.ok is True
    assert "created.txt" in result.changed_files
    changes = change_ledger.list_change_sets(workspace_id="ws-1", thread_id="thread-1")
    assert [change.files[0].path for change in changes] == ["created.txt"]
