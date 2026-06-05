from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from row_bot.migration.row_bot_legacy_rebrand import LEGACY_DATA_DIR_ENV, LEGACY_DATA_DIR_NAME


def test_runtime_persistence_modules_use_row_bot_data_dir(tmp_path):
    fake_home = tmp_path / "home"
    row_bot_data = tmp_path / "row-bot-data"
    fake_home.mkdir()

    code = textwrap.dedent(
        """
        import importlib
        import json
        import pathlib

        from row_bot.migration.row_bot_legacy_rebrand import LEGACY_DATA_DIR_NAME

        def runtime_module(name):
            return importlib.import_module(f"row_bot.{name}")

        modules = [
            "threads",
            "tools.registry",
            "tools.gmail_tool",
            "tools.calendar_tool",
            "tools.x_tool",
            "tts",
            "wiki_vault",
            "mcp_client.config",
            "plugins.loader",
            "plugins.installer",
            "plugins.marketplace",
            "skills",
            "skills_activation",
            "memory_extraction",
            "knowledge_graph",
            "dream_cycle",
            "models",
            "embedding_config",
            "documents",
            "vision",
            "tools.browser_tool",
            "tools.shell_tool",
            "tools.tracker_tool",
            "tools.row_bot_status_tool",
            "developer.storage",
            "designer.storage",
            "designer.brand",
            "designer.fonts",
            "buddy.config",
            "buddy.assets",
            "buddy.hatch",
            "stability",
            "updater",
            "insights",
            "memory_evolution",
            "memory_policy",
        ]
        for name in modules:
            runtime_module(name)

        checks = {
            "threads.DATA_DIR": runtime_module("threads").DATA_DIR,
            "tools.registry.DATA_DIR": runtime_module("tools.registry").DATA_DIR,
            "tools.gmail_tool._GMAIL_DIR": runtime_module("tools.gmail_tool")._GMAIL_DIR,
            "tools.calendar_tool._CALENDAR_DIR": runtime_module("tools.calendar_tool")._CALENDAR_DIR,
            "tools.x_tool._X_DIR": runtime_module("tools.x_tool")._X_DIR,
            "tts._ROW_BOT_DIR": runtime_module("tts")._ROW_BOT_DIR,
            "wiki_vault._DATA_DIR": runtime_module("wiki_vault")._DATA_DIR,
            "mcp_client.config.DATA_DIR": runtime_module("mcp_client.config").DATA_DIR,
            "plugins.loader.DATA_DIR": runtime_module("plugins.loader").DATA_DIR,
            "plugins.installer.DATA_DIR": runtime_module("plugins.installer").DATA_DIR,
            "plugins.marketplace.DATA_DIR": runtime_module("plugins.marketplace").DATA_DIR,
            "skills.DATA_DIR": runtime_module("skills").DATA_DIR,
            "skills_activation.DATA_DIR": runtime_module("skills_activation").DATA_DIR,
            "memory_extraction._DATA_DIR": runtime_module("memory_extraction")._DATA_DIR,
            "knowledge_graph._DATA_DIR": runtime_module("knowledge_graph")._DATA_DIR,
            "dream_cycle._DATA_DIR": runtime_module("dream_cycle")._DATA_DIR,
            "models._DATA_DIR": runtime_module("models")._DATA_DIR,
            "embedding_config.DATA_DIR": runtime_module("embedding_config").DATA_DIR,
            "documents.DATA_DIR": runtime_module("documents").DATA_DIR,
            "vision._DATA_DIR": runtime_module("vision")._DATA_DIR,
            "tools.browser_tool.DATA_DIR": runtime_module("tools.browser_tool").DATA_DIR,
            "tools.shell_tool.DATA_DIR": runtime_module("tools.shell_tool").DATA_DIR,
            "tools.tracker_tool._DATA_DIR": runtime_module("tools.tracker_tool")._DATA_DIR,
            "tools.row_bot_status_tool._DATA_DIR": runtime_module("tools.row_bot_status_tool")._DATA_DIR,
            "developer.storage.DATA_DIR": runtime_module("developer.storage").DATA_DIR,
            "designer.storage.DATA_DIR": runtime_module("designer.storage").DATA_DIR,
            "designer.brand._BRAND_DIR": runtime_module("designer.brand")._BRAND_DIR,
            "designer.fonts._CACHE_DIR": runtime_module("designer.fonts")._CACHE_DIR,
            "buddy.config._DATA_DIR": runtime_module("buddy.config")._DATA_DIR,
            "buddy.assets._DATA_DIR": runtime_module("buddy.assets")._DATA_DIR,
            "buddy.hatch._DATA_DIR": runtime_module("buddy.hatch")._DATA_DIR,
            "stability._DATA_DIR": runtime_module("stability")._DATA_DIR,
            "updater._DATA_DIR": runtime_module("updater")._DATA_DIR,
            "insights._DATA_DIR": runtime_module("insights")._DATA_DIR,
            "memory_evolution._DATA_DIR": runtime_module("memory_evolution")._DATA_DIR,
            "memory_policy._RECALL_TRACE_FILE": runtime_module("memory_policy")._RECALL_TRACE_FILE,
        }

        payload = {
            "home": str(pathlib.Path.home()),
            "legacy_exists": str(pathlib.Path.home() / LEGACY_DATA_DIR_NAME),
            "legacy_created": (pathlib.Path.home() / LEGACY_DATA_DIR_NAME).exists(),
            "checks": {name: str(path) for name, path in checks.items()},
        }
        print(json.dumps(payload))
        """
    )

    env = os.environ.copy()
    env["ROW_BOT_DATA_DIR"] = str(row_bot_data)
    env.pop(LEGACY_DATA_DIR_ENV, None)
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    env["PYTHONPATH"] = os.pathsep.join([str(Path.cwd() / "src"), str(Path.cwd())])

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["home"] == str(fake_home)
    assert payload["legacy_created"] is False
    assert not (fake_home / LEGACY_DATA_DIR_NAME).exists()

    row_bot_root = row_bot_data.resolve()
    for label, raw_path in payload["checks"].items():
        path = Path(raw_path).resolve()
        try:
            path.relative_to(row_bot_root)
        except ValueError as exc:
            raise AssertionError(f"{label} resolved outside ROW_BOT_DATA_DIR: {path}") from exc
