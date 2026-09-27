"""Scenarios run inside the launcher's window script by the fake ``webview``.

Each function receives the fake webview module, the window script's module
(``__main__``) and a report dict the test reads back. Everything the pages
would do goes through the real attested bridge (``native_client_dispatch``).
"""

from __future__ import annotations

import json
import os
from typing import Any


def _config() -> dict[str, Any]:
    path = os.path.join(os.environ["ROW_BOT_DATA_DIR"], "buddy_config.json")
    with open(path, encoding="utf-8") as stream:
        value = json.load(stream)
    return {
        "placement": value.get("placement"),
        "visible": value.get("visible"),
        "overlay": {key: value.get("overlay", {}).get(key) for key in ("x", "y")},
    }


def _picker() -> dict[str, Any]:
    return {"intentId": "intent_1", "intent": "open_existing",
            "conversationId": "conversation-1", "destination": "workspace"}


def overlay_lifecycle(webview: Any, script: Any, report: dict[str, Any]) -> None:
    main = webview.windows[0]
    report["main_options"] = sorted(main.options)
    main.load()
    report["main_discover"] = main.dispatch("discover", {"attestation": "attest-main"})
    report["main_read"] = main.dispatch("buddy_follow", {})
    report["main_publish"] = main.dispatch("buddy_follow", {"conversationId": "conversation-1"})
    report["main_hide"] = main.dispatch("buddy_placement", {"action": "hide"})
    report["tear_off"] = main.dispatch("buddy_placement", {"action": "tear_off", "x": 900, "y": 600})
    report["config_after_tear_off"] = _config()

    buddy = webview.windows[1]
    report["buddy_url"] = buddy.url
    report["buddy_options"] = buddy.options
    report["buddy_exposed"] = sorted(buddy.exposed)
    report["visible_before_ready"] = buddy.visible
    buddy.load()
    report["buddy_discover"] = buddy.dispatch("discover", {"attestation": "attest-buddy"})
    report["buddy_read"] = buddy.dispatch("buddy_follow", {})
    report["buddy_publish"] = buddy.dispatch("buddy_follow", {"conversationId": "conversation-9"})
    report["buddy_tear_off"] = buddy.dispatch("buddy_placement", {"action": "tear_off", "x": 1, "y": 1})
    report["buddy_refused"] = {
        operation: buddy.dispatch(operation, payload)
        for operation, payload in (
            ("select_file", _picker()),
            ("select_folder", _picker()),
            ("clipboard_read", {}),
            ("clipboard_write", {"text": "x"}),
            ("open_external", {"url": "https://example.invalid/"}),
            ("managed_window", {"route": "/app-v2/"}),
            ("save", {"reference": "fixture", "name": "fixture.txt"}),
            ("terminal_open", {"conversationId": None}),
        )
    }
    report["buddy_malformed"] = [
        buddy.dispatch("buddy_follow", {"conversationId": "bad id"}),
        buddy.dispatch("buddy_follow", {"conversationId": 7}),
        buddy.dispatch("buddy_follow", {"conversationId": "c", "extra": 1}),
        buddy.dispatch("main_window", {}),
        buddy.dispatch("main_window", {"conversationId": "../x"}),
        buddy.dispatch("buddy_placement", {"action": "ready", "extra": True}),
        buddy.dispatch("buddy_placement", {"action": "collapse"}),
    ]
    report["ready"] = buddy.dispatch("buddy_placement", {"action": "ready"})
    report["visible_after_ready"] = buddy.visible

    before = len(buddy.scripts)
    report["main_publish_same"] = main.dispatch("buddy_follow", {"conversationId": "conversation-1"})
    report["hints_after_same"] = len(buddy.scripts) - before
    report["main_publish_new"] = main.dispatch("buddy_follow", {"conversationId": "conversation-2"})
    report["hints_after_new"] = buddy.scripts[before:]
    report["buddy_read_new"] = buddy.dispatch("buddy_follow", {})

    main.hide()
    report["show_main"] = buddy.dispatch("main_window", {"conversationId": "conversation-2"})
    report["main_visible"] = main.visible
    report["main_open_scripts"] = [s for s in main.scripts if "row-bot-open-conversation" in s]

    # Closing the main window while Buddy is on the desktop only hides it.
    report["closing_torn_off"] = main.events.closing.fire()
    report["moved"] = buddy.events.moved.fire(1234, 567)
    report["config_after_move"] = _config()

    # Navigating away from the overlay document drops its bridge.
    home = buddy.url
    buddy.url = home.replace("/app-v2/buddy-overlay", "/app-v2/")
    buddy.events.loaded.fire()
    report["bridge_after_navigation"] = buddy.exposed["native_client_dispatch"](
        buddy.proof(), "buddy_placement", {"action": "status"}
    )
    buddy.url = home
    buddy.load()
    buddy.dispatch("discover", {"attestation": "attest-buddy-2"})

    report["hide"] = buddy.dispatch("buddy_placement", {"action": "hide"})
    report["visible_after_hide"] = buddy.visible
    report["config_after_hide"] = _config()
    report["tray_show"] = script._JS_API.show_buddy_window(True)
    report["visible_after_tray_show"] = buddy.visible
    report["dock"] = buddy.dispatch("buddy_placement", {"action": "dock"})
    report["destroyed_after_dock"] = buddy.destroyed
    report["config_after_dock"] = _config()
    report["closing_docked"] = main.events.closing.fire()
    report["main_open_overlay_route"] = main.dispatch("managed_window", {"route": "/app-v2/buddy-overlay"})
    report["main_status"] = main.dispatch("buddy_placement", {"action": "status"})
