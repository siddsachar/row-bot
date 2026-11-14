"""Settings › Devices' count says what its list shows (F7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from row_bot.access.models import AccessDevice, AccessSession, SessionLifetime, TokenFormat
from row_bot.access.service import THIS_COMPUTER_ROUTE
from row_bot.access.store import AccessStore
from row_bot.application.settings_snapshot import _mobile_access


def _signed_in(store: AccessStore, name: str, *, route: str | None, expires: datetime,
               revoked: bool = False) -> None:
    now = datetime.now(timezone.utc)
    store.create_device_record(AccessDevice(
        id=name, display_name=name, created_at=now, last_seen_at=None, revoked_at=None,
        user_agent=None, paired_from=None, access_route=route, legacy_source_id=None))
    store.create_session_record(AccessSession(
        id=f"session-{name}", device_id=name, token_hash="h", token_salt="s",
        token_format=TokenFormat.SESSION_V1, created_at=now, last_seen_at=None, expires_at=expires,
        revoked_at=now if revoked else None, lifetime=SessionLifetime.TEMPORARY,
        replaced_by_session_id=None))


def test_the_count_leaves_out_expired_signed_out_and_this_computers_own_windows(tmp_path) -> None:
    store = AccessStore(tmp_path / "mobile.db")
    store.ensure_schema()
    later = datetime.now(timezone.utc) + timedelta(hours=6)
    _signed_in(store, "phone", route="cli", expires=later)
    _signed_in(store, "laptop", route="cli", expires=later)
    _signed_in(store, "borrowed", route="cli", expires=datetime.now(timezone.utc) - timedelta(hours=1))
    _signed_in(store, "signed-out", route="cli", expires=later, revoked=True)
    _signed_in(store, "window", route=THIS_COMPUTER_ROUTE, expires=later)

    assert _mobile_access(tmp_path) == {
        "availability": "available",
        "active_devices": 2,
        "active_sessions": 2,
    }
