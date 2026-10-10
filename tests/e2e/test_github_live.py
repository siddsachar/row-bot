"""Opt-in live check of GitHub's hosted MCP server through Row-Bot, after a person connected it in
the Row-Bot UI. Their token or OAuth app stays in their keychain: this test never sees, asks for or
prints it, and it only reads (one ``get_me`` call). Nothing here writes to GitHub.

Run it against the data folder of the Row-Bot that was used to connect (see the runbook in the
developer guide), first while connected, then after turning it off, disconnecting and removing it.
The test suite gives every test its own data folder, so the live one is named separately
(``ROW_BOT_GITHUB_LIVE_DATA``) and Row-Bot's modules are re-bound to it for the test.
``ROW_BOT_TEST_MODE=0`` lets Row-Bot read that connection's keychain entry; the folder may never be
your everyday Row-Bot data folder::

    ROW_BOT_TEST_MODE=0 ROW_BOT_GITHUB_LIVE_DATA=<that folder> ROW_BOT_GITHUB_LIVE=connected uv run python -m pytest tests/e2e/test_github_live.py
    ROW_BOT_TEST_MODE=0 ROW_BOT_GITHUB_LIVE_DATA=<that folder> ROW_BOT_GITHUB_LIVE=removed uv run python -m pytest tests/e2e/test_github_live.py
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import time

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.live_provider]
STAGE = os.environ.get("ROW_BOT_GITHUB_LIVE", "")
RECIPES = {"github-hosted", "github-oauth-app"}
# GitHub's token formats: none may ever be written to a configuration, a log or a plan.
TOKEN = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")


def _github_servers() -> dict:
    from row_bot.mcp_client import config
    servers = config.read_saved_configuration().document.get("servers", {})
    return {name: cfg for name, cfg in servers.items() if (cfg.get("source") or {}).get("id") in RECIPES}


def _text_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".log", ".txt", ".toml", ".yaml"} and path.stat().st_size < 64 * 1024 * 1024:
            yield path


@pytest.fixture
def data_dir(reload_for_data_dir) -> Path:
    folder = os.environ.get("ROW_BOT_GITHUB_LIVE_DATA", "")
    if STAGE not in {"connected", "removed"} or not folder or os.environ.get("ROW_BOT_TEST_MODE") != "0":
        pytest.skip("set ROW_BOT_GITHUB_LIVE=connected|removed, ROW_BOT_TEST_MODE=0 and ROW_BOT_GITHUB_LIVE_DATA "
                    "to a live-validation data folder")
    path = Path(folder).resolve()
    everyday = [(Path.home() / name).resolve() for name in (".row-bot", ".thoth")]
    if any(path == root or path.is_relative_to(root) for root in everyday):
        pytest.fail("never point the live check at your everyday Row-Bot data folder")
    if not (path / "mcp_servers.json").is_file():
        pytest.fail("connect GitHub in the Row-Bot that uses this folder first")
    # The keychain entry's service name, the connections and the runtimes are all bound to the folder.
    reload_for_data_dir(path, "row_bot.secret_store", "row_bot.mcp_client.config", "row_bot.mcp_client.requirements")
    return path


def test_no_github_token_is_written_anywhere_in_the_data_folder(data_dir):
    leaked = [str(path.relative_to(data_dir)) for path in _text_files(data_dir)
              if TOKEN.search(path.read_text(encoding="utf-8", errors="ignore"))]
    assert leaked == []  # Paths only: the match itself is never printed.


@pytest.mark.skipif(STAGE != "connected", reason="the connected stage only")
def test_a_connected_github_reads_who_you_are_and_offers_only_read_tools(data_dir):
    from row_bot.mcp_client import runtime
    from row_bot.mcp_client.safety import prefixed_tool_name
    servers = _github_servers()
    assert servers, "connect GitHub in Row-Bot first"
    try:
        runtime.discover_enabled_servers()
        for name, cfg in servers.items():
            assert cfg.get("enabled") and (cfg.get("auth") or {}).get("credential_ref"), f"{name} is not connected and on"
            deadline, status, tools = time.monotonic() + 60, {}, []
            while time.monotonic() < deadline:  # Connected first; its tool list follows a moment later.
                summary = runtime.get_status_summary()
                status, tools = summary["servers"].get(name, {}), summary["tools"].get(name, [])
                if status.get("status") == "failed" or (status.get("status") == "connected" and tools):
                    break
                time.sleep(0.5)
            assert status.get("status") == "connected" and tools, status.get("last_error", "")[:200]
            if cfg.get("input_values", {}).get("read_only", "true") == "true":
                # GitHub's read-only mode: no tool that changes anything is even offered.
                assert not set(runtime.get_destructive_tool_names()) & {prefixed_tool_name(name, t["name"]) for t in tools}
            wrappers = {tool.name: tool for tool in runtime.get_langchain_tools()}
            output = wrappers[prefixed_tool_name(name, "get_me")].invoke({})
            assert not output.startswith("MCP tool error:") and '"login"' in output
    finally:
        runtime.shutdown()


@pytest.mark.skipif(STAGE != "removed", reason="the removed stage only")
def test_a_removed_github_leaves_no_connection_behind(data_dir):
    assert _github_servers() == {}
