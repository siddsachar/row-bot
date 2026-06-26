from __future__ import annotations

import importlib
import json
from pathlib import Path


def test_registry_follows_row_bot_data_dir_after_import(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(first))

    from row_bot.tools import registry

    registry = importlib.reload(registry)
    registry.set_tool_config("filesystem", "workspace_root", "alpha")
    assert (first / "tools_config.json").is_file()

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(second))
    registry.set_tool_config("filesystem", "workspace_root", "beta")

    first_payload = json.loads((first / "tools_config.json").read_text(encoding="utf-8"))
    second_payload = json.loads((second / "tools_config.json").read_text(encoding="utf-8"))
    assert first_payload["tool_configs"]["filesystem"]["workspace_root"] == "alpha"
    assert second_payload["tool_configs"]["filesystem"]["workspace_root"] == "beta"


def test_registry_persists_unicode_config_with_utf8(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))

    from row_bot.tools import registry

    registry = importlib.reload(registry)
    registry.set_tool_config("filesystem", "workspace_root", str(tmp_path / "Row-Bot ⚡"))

    raw = (tmp_path / "tools_config.json").read_bytes()
    assert "Row-Bot ⚡".encode("utf-8") in raw
    payload = json.loads(raw.decode("utf-8"))
    assert payload["tool_configs"]["filesystem"]["workspace_root"].endswith("Row-Bot ⚡")


def test_filesystem_empty_workspace_defaults_to_row_bot_documents(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(Path, "home", lambda: home)

    import row_bot.tools.filesystem_tool as filesystem_tool

    filesystem_tool = importlib.reload(filesystem_tool)
    root = filesystem_tool.FileSystemTool()._get_workspace_root()

    assert root == str(home / "Documents" / "Row-Bot")
    assert (home / "Documents" / "Row-Bot").is_dir()


def test_shell_workspace_root_falls_back_to_developer_thread(tmp_path, monkeypatch):
    import sys

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    for name in [
        "row_bot.threads",
        "row_bot.developer.storage",
    ]:
        sys.modules.pop(name, None)

    import row_bot.threads as threads
    import row_bot.developer.storage as storage
    import row_bot.tools.shell_tool as shell_tool

    threads = importlib.reload(threads)
    storage = importlib.reload(storage)
    monkeypatch.setattr(shell_tool.registry, "get_tool", lambda name: None)

    repo = tmp_path / "repo"
    repo.mkdir()
    workspace = storage.add_or_update_local_workspace(str(repo))
    thread_id = threads.create_thread(
        "Developer",
        thread_type="code",
        developer_workspace_id=workspace.id,
    )

    tool = shell_tool.ShellTool()
    assert tool._get_workspace_root(thread_id) == str(repo.resolve())
    assert tool._get_workspace_root("") is None


def test_pytest_default_data_dir_is_workspace_local():
    data_dir = Path(__import__("os").environ["ROW_BOT_DATA_DIR"]).resolve()
    assert ".tmp" in data_dir.parts
    assert Path.cwd().resolve() in data_dir.parents
    assert "pytest_row_bot" in Path("tests/conftest.py").read_text(encoding="utf-8")
