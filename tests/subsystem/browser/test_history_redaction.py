from __future__ import annotations

import json

from row_bot.tools import browser_tool


def test_browser_history_never_persists_typed_value(tmp_path, monkeypatch) -> None:
    history_path = tmp_path / "browser_history.json"
    monkeypatch.setattr(browser_tool, "_HISTORY_PATH", history_path)
    secret = "token=sk-super-secret@example.com"
    browser_tool.append_browser_history("thread", {"action": "type", "ref": 2, "text_length": len(secret), "submit": True})
    raw = history_path.read_text(encoding="utf-8")
    assert secret not in raw
    assert json.loads(raw)["thread"][0]["text_length"] == len(secret)


def test_snapshot_script_never_serializes_input_values() -> None:
    source = browser_tool._build_snapshot_js(100)
    assert 'value="${value}"' not in source
    assert "value_length" in source
