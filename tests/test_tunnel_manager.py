from __future__ import annotations

import sys
import types


def test_ngrok_authtoken_uses_saved_row_bot_key(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.tunnel as tunnel

    monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
    monkeypatch.setattr(api_keys, "get_key", lambda name: " saved-token " if name == "NGROK_AUTHTOKEN" else "")

    assert tunnel._ngrok_authtoken() == "saved-token"


def test_ngrok_authtoken_falls_back_to_environment_when_key_store_fails(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.tunnel as tunnel

    monkeypatch.setenv("NGROK_AUTHTOKEN", "env-token")

    def _raise(_name: str) -> str:
        raise RuntimeError("key store unavailable")

    monkeypatch.setattr(api_keys, "get_key", _raise)

    assert tunnel._ngrok_authtoken() == "env-token"


def test_ngrok_configuration_reports_unreadable_saved_key(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.tunnel as tunnel

    monkeypatch.setitem(sys.modules, "pyngrok", types.SimpleNamespace())
    monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
    monkeypatch.setattr(api_keys, "get_key", lambda name: "")
    monkeypatch.setattr(api_keys, "key_status", lambda name: {"configured": True})

    status, detail = tunnel.ngrok_configuration_status()

    assert status == "error"
    assert "keyring secret is unreadable" in detail
