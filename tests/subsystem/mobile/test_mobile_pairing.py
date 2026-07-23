from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI
from starlette.testclient import TestClient

from row_bot.mobile import routes as mobile_routes
from row_bot.mobile.auth import PairingError, create_pairing_ticket
from row_bot.mobile.routes import register_mobile_routes
from row_bot.mobile.store import MobileAuthStore


def _app(tmp_path) -> FastAPI:
    app = FastAPI()
    register_mobile_routes(app, store=MobileAuthStore(tmp_path / "mobile.db"))
    return app


def test_pairing_routes_create_session_cookie_without_exposing_token_in_body(tmp_path) -> None:
    app = _app(tmp_path)
    desktop = TestClient(app, client=("127.0.0.1", 50000))

    start = desktop.post(
        "/api/mobile/pair/start",
        json={"intended_origin": "http://phone.test", "access_mode": "lan"},
    )
    assert start.status_code == 200
    code = start.json()["pairing"]["code"]
    assert start.json()["pairing"]["pairing_url"] == f"http://phone.test/mobile/pair?code={code}"

    phone = TestClient(app, base_url="http://phone.test", client=("192.168.1.25", 50000))
    confirm = phone.post(
        "/api/mobile/pair/confirm",
        json={"code": code, "display_name": "Android Chrome"},
        headers={"user-agent": "pytest-mobile"},
    )

    assert confirm.status_code == 200
    body = confirm.json()
    assert body["authenticated"] is True
    assert body["device"]["display_name"] == "Android Chrome"
    assert "token" not in body
    assert "set-cookie" in confirm.headers
    assert "row_bot_mobile_lan=" in confirm.headers["set-cookie"]
    cookie_value = confirm.cookies.get("row_bot_mobile_lan")
    assert cookie_value
    assert cookie_value not in confirm.text

    session = phone.get("/api/mobile/session", cookies={"row_bot_mobile_lan": cookie_value})
    assert session.status_code == 200
    assert session.json()["authenticated"] is True


def test_pairing_form_redirects_to_mobile_shell_with_cookie(tmp_path) -> None:
    app = _app(tmp_path)
    desktop = TestClient(app, client=("127.0.0.1", 50000))
    code = desktop.post(
        "/api/mobile/pair/start",
        json={"intended_origin": "http://phone.test", "access_mode": "lan"},
    ).json()["pairing"]["code"]

    phone = TestClient(app, base_url="http://phone.test", client=("192.168.1.25", 50000))
    confirm = phone.post(
        "/api/mobile/pair/confirm",
        data={"code": code, "display_name": "iPhone Safari"},
        follow_redirects=False,
    )

    assert confirm.status_code == 303
    assert confirm.headers["location"] == "/?mobile=1"
    assert "row_bot_mobile_lan=" in confirm.headers["set-cookie"]


def test_pairing_page_is_not_cached(tmp_path) -> None:
    app = _app(tmp_path)
    desktop = TestClient(app, client=("127.0.0.1", 50000))
    code = desktop.post("/api/mobile/pair/start", json={}).json()["pairing"]["code"]
    phone = TestClient(app, base_url="http://phone.test", client=("192.168.1.25", 50000))

    response = phone.get(f"/mobile/pair?code={code}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-robots-tag"] == "noindex"
    assert "Pairing links are single-use and expire after 10 minutes." in response.text
    assert '<form method="post" action="/api/mobile/pair/confirm">' in response.text
    assert f'name="code" value="{code}"' in response.text
    assert "Pair device" in response.text


def test_pairing_page_without_code_shows_recovery_without_dead_form(tmp_path) -> None:
    app = _app(tmp_path)
    phone = TestClient(app, base_url="http://phone.test", client=("192.168.1.25", 50000))

    response = phone.get("/mobile/pair")

    assert response.status_code == 200
    assert "This pairing link is invalid or incomplete." in response.text
    assert "create a new QR code" in response.text
    assert "<form" not in response.text
    assert "Pair device" not in response.text
    assert 'type="hidden" name="code"' not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-robots-tag"] == "noindex"


def test_pairing_form_failure_renders_pairing_page(tmp_path) -> None:
    app = _app(tmp_path)
    phone = TestClient(app, base_url="http://phone.test", client=("192.168.1.25", 50000))

    response = phone.post(
        "/api/mobile/pair/confirm",
        data={"code": "not-a-real-code", "display_name": "Phone"},
    )

    assert response.status_code == 400
    assert "This pairing link is invalid or incomplete." in response.text
    assert "Pair Row-Bot" in response.text
    assert "create a new QR code" in response.text
    assert "<form" not in response.text
    assert "Pair device" not in response.text
    assert 'type="hidden" name="code"' not in response.text


def test_pairing_form_failure_explains_expired_code(tmp_path) -> None:
    store = MobileAuthStore(tmp_path / "mobile.db")
    app = FastAPI()
    register_mobile_routes(app, store=store)
    ticket = create_pairing_ticket(store, ttl=timedelta(seconds=-1))
    phone = TestClient(app, base_url="http://phone.test", client=("192.168.1.25", 50000))

    response = phone.post(
        "/api/mobile/pair/confirm",
        data={"code": ticket.code, "display_name": "Phone"},
    )

    assert response.status_code == 400
    assert "This pairing code has expired." in response.text
    assert "create a new QR code" in response.text
    assert "<form" not in response.text
    assert "Pair device" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_pair_start_rejects_forwarded_localhost_bypass(tmp_path) -> None:
    app = _app(tmp_path)
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post(
        "/api/mobile/pair/start",
        json={"intended_origin": "https://evil.example"},
        headers={"x-forwarded-for": "203.0.113.4"},
    )

    assert response.status_code == 403
    assert response.json()["error"] == "local_required"


def test_pair_confirm_rejects_reused_code(tmp_path) -> None:
    app = _app(tmp_path)
    desktop = TestClient(app, client=("127.0.0.1", 50000))
    code = desktop.post("/api/mobile/pair/start", json={}).json()["pairing"]["code"]

    phone = TestClient(app, client=("192.168.1.25", 50000))
    first = phone.post("/api/mobile/pair/confirm", json={"code": code, "display_name": "Phone"})
    second = phone.post("/api/mobile/pair/confirm", json={"code": code, "display_name": "Phone"})

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "already_claimed"


def test_reused_pairing_form_shows_terminal_recovery_state(tmp_path) -> None:
    app = _app(tmp_path)
    desktop = TestClient(app, client=("127.0.0.1", 50000))
    code = desktop.post("/api/mobile/pair/start", json={}).json()["pairing"]["code"]
    phone = TestClient(app, client=("192.168.1.25", 50000))
    assert phone.post("/api/mobile/pair/confirm", json={"code": code, "display_name": "Phone"}).status_code == 200

    response = phone.post(
        "/api/mobile/pair/confirm",
        data={"code": code, "display_name": "Phone"},
    )

    assert response.status_code == 400
    assert "This pairing code was already used." in response.text
    assert "<form" not in response.text
    assert "Pair device" not in response.text


def test_locked_pairing_form_shows_terminal_recovery_state(tmp_path, monkeypatch) -> None:
    def _raise_locked(*_args, **_kwargs):
        raise PairingError("locked")

    app = _app(tmp_path)
    phone = TestClient(app, client=("192.168.1.25", 50000))
    monkeypatch.setattr(mobile_routes, "confirm_pairing", _raise_locked)

    response = phone.post(
        "/api/mobile/pair/confirm",
        data={"code": "rbp_locked.ticket", "display_name": "Phone"},
    )

    assert response.status_code == 400
    assert "temporarily locked" in response.text
    assert "<form" not in response.text
    assert "Pair device" not in response.text


def test_revoke_device_blocks_next_session_validation(tmp_path) -> None:
    app = _app(tmp_path)
    desktop = TestClient(app, client=("127.0.0.1", 50000))
    code = desktop.post("/api/mobile/pair/start", json={}).json()["pairing"]["code"]
    phone = TestClient(app, client=("192.168.1.25", 50000))
    confirm = phone.post("/api/mobile/pair/confirm", json={"code": code, "display_name": "Phone"})
    cookie_value = confirm.cookies.get("row_bot_mobile_lan")
    device_id = confirm.json()["device"]["id"]

    revoke = desktop.post(f"/api/mobile/devices/{device_id}/revoke")
    assert revoke.status_code == 200
    assert revoke.json()["revoked"] is True

    session = phone.get("/api/mobile/session", cookies={"row_bot_mobile_lan": cookie_value})
    assert session.status_code == 200
    assert session.json()["authenticated"] is False


def test_device_and_event_management_requires_local_or_settings_session(tmp_path) -> None:
    app = _app(tmp_path)
    remote = TestClient(app, client=("192.168.1.25", 50000))

    devices = remote.get("/api/mobile/devices")
    events = remote.get("/api/mobile/access-events")

    assert devices.status_code == 403
    assert events.status_code == 403
