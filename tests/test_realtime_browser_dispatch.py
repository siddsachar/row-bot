from __future__ import annotations

from pathlib import Path

from row_bot.ui.state import P
from row_bot.ui.streaming import run_realtime_client_js


ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    instances: dict[str, "FakeClient"] = {}

    def __init__(self, client_id: str = "client-1") -> None:
        self.id = client_id
        self.scripts: list[str] = []
        self._deleted = False
        self.__class__.instances[client_id] = self

    def run_javascript(self, code: str) -> None:
        self.scripts.append(code)


def test_realtime_browser_js_uses_captured_client_without_ui_context():
    client = FakeClient()
    p = P()
    p.realtime_client = client

    assert run_realtime_client_js(p, "window.testRealtimeSpeech = true;", context="test") is True
    assert client.scripts == ["window.testRealtimeSpeech = true;"]


def test_realtime_browser_js_returns_false_for_deleted_client():
    client = FakeClient("client-2")
    client._deleted = True
    p = P()
    p.realtime_client = client

    assert run_realtime_client_js(p, "window.testRealtimeSpeech = true;", context="test") is False
    assert client.scripts == []


def test_realtime_speech_wiring_uses_captured_client_helper():
    streaming_src = (ROOT / "src" / "row_bot" / "ui" / "streaming.py").read_text(encoding="utf-8")
    chat_src = (ROOT / "src" / "row_bot" / "ui" / "chat.py").read_text(encoding="utf-8")
    components_src = (ROOT / "src" / "row_bot" / "ui" / "chat_components.py").read_text(encoding="utf-8")

    assert "run_realtime_client_js(" in streaming_src
    assert "ui.run_javascript(speak_realtime_js(text))" not in streaming_src
    for source in (chat_src, components_src):
        assert "p.realtime_client = ui.context.client" in source
        assert "run_realtime_client_js(" in source


def test_native_webview_uses_persistent_storage_for_microphone_grants():
    launcher_src = (ROOT / "src" / "row_bot" / "launcher.py").read_text(encoding="utf-8")

    assert "ROW_BOT_WEBVIEW_STORAGE_PATH" in launcher_src
    assert '"browser_profile"' in launcher_src
    assert '"pywebview"' in launcher_src
    assert "private_mode=False" in launcher_src
    assert "storage_path=_WEBVIEW_STORAGE_PATH" in launcher_src
