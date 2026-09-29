"""Behaviour of the launcher's native window script around the desktop Buddy.

The real window script (``launcher._WINDOW_SCRIPT``) runs in a subprocess
against a recording fake ``webview`` and a fake loopback API that answers the
native attestation routes. Pages are simulated through the real attested
bridge, so URL, window options, bridge roles and lifecycle are all exercised.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from row_bot import launcher

pytestmark = pytest.mark.subsystem

ROOT = Path(__file__).resolve().parents[3]
FAKE_WEBVIEW = ROOT / "tests" / "fixtures" / "fake_webview"
SCENARIOS = Path(__file__).with_name("launcher_window_scenarios.py")


class _NativeApi(BaseHTTPRequestHandler):
    """Answers only the native routes the window script calls."""

    calls: list[str] = []
    external: list[dict] = []

    def log_message(self, *_args):
        pass

    def _json(self, value):
        body = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.calls.append("GET " + self.path)
        if self.path == "/api/v1/native/bootstrap":
            self._json({"instance_id": "instance-1"})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.calls.append("POST " + self.path + " " + str(body.get("window_id", "")))
        if self.path == "/api/v1/native/attest":
            self._json({"session_id": "session-1", "policy_revision": "policy-1",
                        "authority_grant": "grant-" + body["window_id"]})
        elif self.path == "/api/v1/native/authorize":
            self._json({"ok": body.get("authority_grant") == "grant-" + body.get("window_id", "")})
        elif self.path == "/api/v1/native/revoke":
            self._json({"revoked": True})
        elif self.path == "/api/v1/native/terminal/external":
            self.external.append(body)
            self._json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def api():
    _NativeApi.calls = []
    _NativeApi.external = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _NativeApi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _run(tmp_path: Path, port: int, scenario: str, *, client_v2: bool = True,
         control_port: int = 0, buddy_config: dict | None = None) -> dict:
    data = tmp_path / "data"
    data.mkdir()
    if buddy_config is not None:
        (data / "buddy_config.json").write_text(json.dumps(buddy_config), encoding="utf-8")
    report = tmp_path / "report.json"
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("ROW_BOT_", "FAKE_WEBVIEW_"))
    }
    environment.update({
        "PYTHONPATH": os.pathsep.join([str(FAKE_WEBVIEW), str(ROOT / "src")]),
        "ROW_BOT_DATA_DIR": str(data),
        "ROW_BOT_TEST_MODE": "1",
        "FAKE_WEBVIEW_SCENARIO": scenario,
        "FAKE_WEBVIEW_SCENARIO_FILE": str(SCENARIOS),
        "FAKE_WEBVIEW_REPORT": str(report),
    })
    completed = subprocess.run(
        [sys.executable, "-", f"http://127.0.0.1:{port}/app-v2/", "Row-Bot",
         "1280", "900", "", str(control_port), "1" if client_v2 else "0"],
        input=launcher._WINDOW_SCRIPT,
        text=True,
        capture_output=True,
        env=environment,
        cwd=tmp_path,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    value = json.loads(report.read_text(encoding="utf-8"))
    assert "error" not in value, value["error"]
    return value


def _ok(value):
    return value.get("status") == "ok"


def test_tear_off_opens_the_react_overlay_with_its_own_restricted_bridge(tmp_path, api) -> None:
    port = api.server_address[1]
    report = _run(tmp_path, port, "overlay_lifecycle")

    # The main window keeps its attested bridge (no legacy js_api).
    assert "js_api" not in report["main_options"]
    assert set(report["main_discover"]["value"]["capabilities"]) >= {
        "buddy_placement", "buddy_follow", "managed_window", "clipboard_read",
        "terminal_open", "terminal_external"}
    # Open in your terminal: the host posts the document's grant and only the
    # conversation id; the server picks the folder. Paths are refused.
    assert report["main_terminal_external"] == {"status": "ok", "value": None}
    assert report["main_terminal_external_path"] == {"status": "unavailable", "reason": "invalid_request"}
    [external] = api.RequestHandlerClass.external
    assert external == {"session_id": "session-1", "policy_revision": "policy-1",
                        "authority_grant": "grant-" + external["window_id"],
                        "instance_id": "instance-1", "window_id": external["window_id"],
                        "window_epoch": external["window_epoch"], "conversation_id": "conversation-1"}
    # Main windows publish what they show; only Buddy reads it or hides itself.
    assert report["main_read"]["status"] == "unavailable"
    assert report["main_publish"] == {"status": "ok", "value": {"conversationId": "conversation-1", "revision": 1}}
    assert report["main_hide"]["status"] == "unavailable"

    # Tear-off: React route, frameless always-on-top 380x230, hidden until ready.
    assert report["tear_off"] == {"status": "ok", "value": {"placement": "desktop", "visible": True}}
    assert report["config_after_tear_off"] == {"placement": "desktop", "visible": True, "overlay": {"x": 710, "y": 524}}
    assert report["buddy_url"] == f"http://127.0.0.1:{port}/app-v2/buddy-overlay"
    options = report["buddy_options"]
    assert "js_api" not in options
    assert {key: options[key] for key in ("width", "height", "frameless", "on_top", "hidden",
                                          "resizable", "easy_drag", "x", "y")} == {
        "width": 380, "height": 230, "frameless": True, "on_top": True, "hidden": True,
        "resizable": False, "easy_drag": False, "x": 710, "y": 524}
    assert options["transparent"] is (sys.platform != "win32")
    assert report["buddy_exposed"] == ["native_client_dispatch"]
    assert report["visible_before_ready"] is False

    # The overlay document gets exactly its three operations.
    assert sorted(report["buddy_discover"]["value"]["capabilities"]) == [
        "buddy_follow", "buddy_placement", "main_window"]
    assert report["buddy_read"] == {"status": "ok", "value": {"conversationId": "conversation-1", "revision": 1}}
    assert report["buddy_publish"]["status"] == "unavailable"
    assert report["buddy_tear_off"]["status"] == "unavailable"
    assert all(value["status"] == "unavailable" for value in report["buddy_refused"].values()), report["buddy_refused"]
    assert [value["status"] for value in report["buddy_malformed"]] == ["unavailable"] * 7
    assert report["ready"] == {"status": "ok", "value": {"placement": "desktop", "visible": True}}
    assert report["visible_after_ready"] is True

    # Follow: only a real change bumps the revision and nudges the overlay,
    # and the nudge carries no data.
    assert report["main_publish_same"]["value"]["revision"] == 1
    assert report["hints_after_same"] == 0
    assert report["main_publish_new"]["value"] == {"conversationId": "conversation-2", "revision": 2}
    assert report["hints_after_new"] == ["window.dispatchEvent(new Event('row-bot-native-changed'));"]
    assert report["buddy_read_new"]["value"] == {"conversationId": "conversation-2", "revision": 2}

    # Open full thread: the main window comes forward and is asked to open it.
    assert _ok(report["show_main"]) and report["main_visible"] is True
    assert report["main_open_scripts"] == [
        "window.dispatchEvent(new CustomEvent('row-bot-open-conversation', { detail: \"conversation-2\" }));"]
    assert report["closing_torn_off"] == [False]
    # pywebview reports moves in physical pixels; the host keeps DIPs (B100).
    scale = report["scale"]
    assert report["config_after_move"]["overlay"] == {"x": round(1234 / scale), "y": round(567 / scale)}

    assert report["bridge_after_navigation"]["status"] == "unavailable"
    assert _ok(report["hide"]) and report["visible_after_hide"] is False
    assert report["config_after_hide"]["visible"] is False
    assert report["tray_show"] is True and report["visible_after_tray_show"] is True
    # Docking closes the overlay window (its own document is gone, so its
    # bridge answers "unavailable" for the call that closed it).
    assert report["destroyed_after_dock"] is True
    assert report["config_after_dock"]["placement"] == "docked"
    assert report["closing_docked"] == [True]
    assert report["main_open_overlay_route"]["status"] == "unavailable"
    assert report["main_status"]["value"] == {"placement": "docked", "visible": True}

    # Every document exchanged its own attestation; nothing else was called.
    assert {call.split(" ")[1] for call in api.RequestHandlerClass.calls} <= {
        "/api/v1/native/bootstrap", "/api/v1/native/attest", "/api/v1/native/authorize",
        "/api/v1/native/revoke", "/api/v1/native/terminal/external"}


def test_window_script_points_the_overlay_at_the_react_route() -> None:
    namespace = {"buddy_overlay_url": __import__(
        "row_bot.buddy.native_host", fromlist=["buddy_overlay_url"]).buddy_overlay_url}
    source = launcher._WINDOW_SCRIPT
    start = source.index("def _buddy_overlay_url(port):")
    end = source.index("\ndef ", start + 1)
    exec(compile(source[start:end], "<window-script>", "exec"), namespace)
    assert namespace["_buddy_overlay_url"](8765) == "http://127.0.0.1:8765/app-v2/buddy-overlay"


def test_start_up_docks_buddy_first_and_the_tray_controls_follow_placement(tmp_path, api) -> None:
    """Tear-off is session-scoped; the tray's loopback control routes show,
    hide and close only a torn-off Buddy, and only manual changes persist."""
    report = _run(tmp_path, api.server_address[1], "startup_and_tray", control_port=_free_port(),
                  buddy_config={"placement": "desktop", "visible": True, "overlay": {"x": 300, "y": 200}})
    assert report["main_created_with"] == {"placement": "docked", "visible": True}
    assert report["config_at_start"] == {"placement": "docked", "visible": True, "overlay": {"x": 300, "y": 200}}
    if sys.platform == "win32":
        assert report["per_monitor_v2"] is True
    assert report["show_while_docked"] == [409, {"ok": False}]
    assert report["windows_while_docked"] == 1
    assert report["visible_after_ready"] is True
    assert report["hide_automatic"] == [200, {"ok": True}]
    assert report["after_hide_automatic"] == [False, True]
    assert report["show_automatic"] == [200, {"ok": True}]
    assert report["hide_manual"] == [200, {"ok": True}]
    assert report["after_hide_manual"] == [False, False]
    assert report["show_manual"] == [200, {"ok": True}]
    assert report["after_show_manual"] == [True, True]
    assert report["main_show"] == [200, {"ok": True}] and report["main_visible"] is True
    assert report["close"] == [200, {"ok": True}]
    assert report["after_close"] == [True, "desktop"]
    assert report["unknown"] == [404, None]
