"""``row-bot run``'s command line: what it reads, what it refuses, and the data folder a run gets.

Nothing here starts a run (``tests/subsystem/agents/test_headless_run.py`` drives the turns, and
``test_headless_run_process.py`` runs the real command); a command line that can't start a run exits 2
before anything is loaded or written.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from row_bot import launcher
from row_bot.headless.cli import EXIT_USAGE, options_from_args, prepare_process

pytestmark = [pytest.mark.subsystem, pytest.mark.installer, pytest.mark.platform]


def _options(*argv: str):
    return options_from_args(launcher._build_arg_parser().parse_args(["run", *argv]))


def _contents(folder: Path) -> dict[str, int]:
    return {str(path.relative_to(folder)): path.stat().st_size for path in folder.rglob("*") if path.is_file()}


def test_a_run_reads_its_task_endpoint_and_limits_from_its_flags(tmp_path, monkeypatch):
    task = tmp_path / "task.txt"
    task.write_text("Make hello.txt say hi.", encoding="utf-8")
    monkeypatch.setenv("BENCH_KEY", "not-a-real-key")

    options = _options("--task-file", str(task), "--workspace", str(tmp_path), "--model", "glm-5.3-flash",
                       "--base-url", "https://models.example/v1/", "--api-style", "anthropic",
                       "--api-key-env", "BENCH_KEY", "--context-window", "131072", "--max-output-tokens", "8192",
                       "--reasoning-effort", "High", "--timeout", "900", "--max-rounds", "200",
                       "--approval", "hands-off", "--output-dir", str(tmp_path / "out"))

    assert options.task == "Make hello.txt say hi."
    assert options.workspace == tmp_path.resolve()
    assert (options.model, options.base_url, options.api_style) == ("glm-5.3-flash", "https://models.example/v1",
                                                                     "anthropic")
    assert options.api_key_env == "BENCH_KEY"
    assert (options.context_window, options.max_output_tokens, options.reasoning_effort) == (131072, 8192, "high")
    assert (options.timeout, options.max_rounds) == (900.0, 200)
    assert (options.approval, options.approval_mode) == ("hands-off", "full")
    assert options.output_dir == (tmp_path / "out").resolve() and options.data_dir is None


def test_the_endpoint_and_limits_can_come_from_the_environment_but_hands_off_cannot(tmp_path, monkeypatch):
    for name, value in {"MODEL": "qwen3-coder", "BASE_URL": "http://127.0.0.1:9/v1", "API_STYLE": "responses",
                        "CONTEXT_WINDOW": "65536", "MAX_OUTPUT_TOKENS": "4096", "APPROVAL": "hands-off"}.items():
        monkeypatch.setenv(f"ROW_BOT_RUN_{name}", value)

    options = _options("--task", "Hi", "--workspace", str(tmp_path))

    assert (options.model, options.base_url, options.api_style) == ("qwen3-coder", "http://127.0.0.1:9/v1",
                                                                     "responses")
    assert (options.context_window, options.max_output_tokens) == (65536, 4096)
    assert (options.approval, options.approval_mode) == ("ask", "approve")


@pytest.mark.parametrize("argv", [
    ["--task", "Hi"],  # no model
    ["--task", "Hi", "--model", "m", "--api-style", "grpc"],
    ["--task-file", "{tmp}/missing.txt", "--model", "m"],
    ["--task", "   ", "--model", "m"],
    ["--task", "Hi", "--model", "m", "--workspace", "{tmp}/not-a-folder"],
    ["--task", "Hi", "--model", "m", "--base-url", "ftp://models.example"],
    ["--task", "Hi", "--model", "m", "--api-key-env", "ROW_BOT_TEST_UNSET_KEY_VARIABLE"],
    ["--task", "Hi", "--model", "m", "--context-window", "0"],
    ["--task", "Hi", "--task-file", "{tmp}/task.txt", "--model", "m"],
])
def test_a_command_line_that_cant_start_a_run_exits_2_and_writes_nothing(tmp_path, monkeypatch, argv):
    monkeypatch.delenv("ROW_BOT_RUN_MODEL", raising=False)
    monkeypatch.delenv("ROW_BOT_TEST_UNSET_KEY_VARIABLE", raising=False)
    monkeypatch.chdir(tmp_path)
    data_dir = Path(os.environ["ROW_BOT_DATA_DIR"])
    before = _contents(data_dir)

    with pytest.raises(SystemExit) as stopped:
        launcher.main(["run", *[part.replace("{tmp}", str(tmp_path)) for part in argv]])

    assert stopped.value.code == EXIT_USAGE
    assert os.environ["ROW_BOT_DATA_DIR"] == str(data_dir)
    assert _contents(data_dir) == before
    assert list(tmp_path.iterdir()) == []


def test_help_loads_and_writes_nothing(capsys):
    data_dir = Path(os.environ["ROW_BOT_DATA_DIR"])
    before = _contents(data_dir)

    with pytest.raises(SystemExit) as stopped:
        launcher.main(["run", "--help"])

    assert stopped.value.code == 0
    assert "--base-url" in capsys.readouterr().out
    assert _contents(data_dir) == before


@pytest.fixture
def _process_environment(monkeypatch):
    """prepare_process sets this process's environment: put it back afterwards."""
    for name in ("ROW_BOT_DATA_DIR", "ROW_BOT_WORKSPACE", "ROW_BOT_INSTALL_COUNT"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    monkeypatch.setenv("ROW_BOT_INSTALL_COUNT", "1")


def test_each_run_gets_a_fresh_throwaway_data_folder_with_counting_off(tmp_path, _process_environment):
    profile = os.environ["ROW_BOT_DATA_DIR"]
    options = _options("--task", "Hi", "--model", "m", "--workspace", str(tmp_path))
    first, first_throwaway = prepare_process(options)
    second, second_throwaway = prepare_process(options)
    try:
        assert first_throwaway and second_throwaway
        assert first != second and str(first) != profile and str(second) != profile
        assert os.environ["ROW_BOT_DATA_DIR"] == str(second)
        assert os.environ["ROW_BOT_WORKSPACE"] == str(tmp_path.resolve())
        assert os.environ["ROW_BOT_INSTALL_COUNT"] == "0"
    finally:
        shutil.rmtree(first, ignore_errors=True)
        shutil.rmtree(second, ignore_errors=True)


def test_learning_is_off_in_a_throwaway_data_folder_whatever_its_default(tmp_path, monkeypatch, _process_environment):
    from dataclasses import replace

    from row_bot import learning

    # As in a build where Learning exists and is on by default (the 5.4 cut): a fresh folder of its own reads as on.
    monkeypatch.setattr(learning, "_PREVIEW", True, raising=False)
    monkeypatch.setattr(learning, "DEFAULTS", replace(learning.DEFAULTS, enabled=True))
    learning.invalidate()
    (tmp_path / "fresh").mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "fresh"))
    assert learning.is_enabled()

    data_dir, throwaway = prepare_process(_options("--task", "Hi", "--model", "m", "--workspace", str(tmp_path)))
    try:
        assert throwaway and not learning.is_enabled()
    finally:
        learning.invalidate()
        shutil.rmtree(data_dir, ignore_errors=True)


def test_a_named_data_folder_is_used_and_kept(tmp_path, _process_environment):
    options = _options("--task", "Hi", "--model", "m", "--workspace", str(tmp_path),
                       "--data-dir", str(tmp_path / "bench-profile"))

    data_dir, throwaway = prepare_process(options)

    assert (data_dir, throwaway) == ((tmp_path / "bench-profile").resolve(), False)
    assert data_dir.is_dir() and os.environ["ROW_BOT_DATA_DIR"] == str(data_dir)
