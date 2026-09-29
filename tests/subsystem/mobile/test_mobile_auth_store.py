from __future__ import annotations

from row_bot.mobile.store import MobileAuthStore


def _store(tmp_path) -> MobileAuthStore:
    return MobileAuthStore(tmp_path / "mobile.db")


def test_access_events_are_display_safe(tmp_path) -> None:
    store = _store(tmp_path)

    event = store.log_event(
        "paired",
        device_id="device-1",
        ip="127.0.0.1",
        user_agent="pytest",
        detail={"access_mode": "localhost"},
    )

    assert store.recent_events()[0].id == event.id
    assert store.recent_events()[0].to_public_dict()["detail"] == {"access_mode": "localhost"}
