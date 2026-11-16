"""`row-bot mcp` as an editor starts it: a real process speaking MCP on stdin and stdout.

With Row-Bot closed it still lists its tools, and every call says Row-Bot isn't running. Nothing
but protocol messages reaches stdout, and it writes nothing into the data folder (not even the
launcher's log, which every other start writes).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from row_bot.editors import catalog

pytestmark = [pytest.mark.subsystem, pytest.mark.platform, pytest.mark.slow]


def test_row_bot_mcp_answers_an_editor_while_row_bot_is_closed(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    environment = {**os.environ, "ROW_BOT_DATA_DIR": str(data), "ROW_BOT_INSTALL_COUNT": "0",
                   "ROW_BOT_TEST_MODE": "1", "PYTHONIOENCODING": "utf-8"}
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "zed"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "ask_row_bot", "arguments": {"message": "Hello"}}},
    ]
    finished = subprocess.run(
        [sys.executable, "-m", "row_bot.launcher", "mcp", "--editor", "zed"],
        input="".join(json.dumps(message) + "\n" for message in messages).encode("utf-8"),
        capture_output=True, env=environment, timeout=120, check=False,
    )
    assert finished.returncode == 0, finished.stderr.decode("utf-8", "replace")
    replies = [json.loads(line) for line in finished.stdout.decode("utf-8").splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2, 3]
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    assert {tool["name"] for tool in replies[1]["result"]["tools"]} == set(catalog.TOOL_NAMES)
    assert replies[2]["result"] == {"content": [{"type": "text", "text": catalog.NOT_RUNNING}], "isError": True}
    assert sorted(path.name for path in data.rglob("*")) == []
