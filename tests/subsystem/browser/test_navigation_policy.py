from __future__ import annotations

from row_bot.tools.browser_tool import _history_url, _navigation_policy


def test_suspicious_exfiltration_url_blocks() -> None:
    policy, _ = _navigation_policy("https://example.com/?data=" + "A" * 600)
    assert policy == "block"


def test_private_network_and_cross_origin_query_require_approval() -> None:
    assert _navigation_policy("http://127.0.0.1/admin")[0] == "ask"
    assert _navigation_policy("https://other.example/path?q=hello", "https://first.example/")[0] == "ask"


def test_history_url_removes_query_and_fragment() -> None:
    value = _history_url("https://example.com/path?token=secret#private")
    assert value == "https://example.com/path"
    assert "secret" not in value
