"""Notifications on this device (D8): only over HTTPS, only to browser push services."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from row_bot.access import push
from row_bot.access.config import AccessConfig
from row_bot.access.middleware import AccessMiddleware
from row_bot.access.routes import register_access_routes
from row_bot.access.service import AccessService
from row_bot.access.store import AccessStore

FCM = "https://fcm.googleapis.com/fcm/send/device-token"


def _phone(tmp_path, monkeypatch, origin: str):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    host = origin.split("://", 1)[1].split(":", 1)[0]
    config = AccessConfig.build(deployment_mode="server", allowed_hosts=(host,))
    service = AccessService(AccessStore(tmp_path / "access.db"))
    app = FastAPI()
    registration = register_access_routes(app, service=service, config=config)
    app.add_middleware(
        AccessMiddleware,
        config=config,
        session_authenticator=registration.authenticator,
    )
    client = TestClient(app, base_url=origin, client=("127.0.0.1", 51000), follow_redirects=False)
    created = service.create_invitation(intended_origin=origin)
    claimed = service.claim_invitation(created.token, intended_origin=origin, display_name="Pixel")
    names = registration.cookies.names
    client.cookies.set(names.https if origin.startswith("https") else names.http, claimed.session_token)
    return client, claimed.device.id


def _subscribe(client, origin: str, endpoint: str = FCM):
    return client.post(
        "/api/access/devices/push/subscribe",
        json={"endpoint": endpoint, "keys": {"p256dh": "BKeyOfThisBrowser", "auth": "auth-secret"}},
        headers={"origin": origin},
    )


def test_a_phone_signed_in_over_https_turns_notifications_on_and_off(tmp_path, monkeypatch) -> None:
    origin = "https://row-bot.example"
    client, device_id = _phone(tmp_path, monkeypatch, origin)

    status = client.get("/api/access/devices/push").json()
    assert status["available"] is True and status["subscribed"] is False
    assert status["public_key"]

    assert _subscribe(client, origin).status_code == 200
    assert client.get("/api/access/devices/push").json()["subscribed"] is True
    saved = push.PushStore(tmp_path / "data").subscriptions()
    assert [(item.device_id, item.endpoint, item.origin) for item in saved] == [(device_id, FCM, origin)]

    off = client.post("/api/access/devices/push/unsubscribe", headers={"origin": origin})
    assert off.json()["subscribed"] is False
    assert push.PushStore(tmp_path / "data").subscriptions() == []


def test_a_phone_on_plain_http_is_told_notifications_need_https(tmp_path, monkeypatch) -> None:
    origin = "http://row-bot.lan:8080"
    client, _device_id = _phone(tmp_path, monkeypatch, origin)

    status = client.get("/api/access/devices/push").json()
    assert status == {"ok": True, "available": False, "reason": "needs_https", "show_details": True}
    assert _subscribe(client, origin).status_code == 409
    assert not (tmp_path / "data" / push.VAPID_KEY_FILENAME).exists()


def test_only_a_browser_push_service_and_the_same_origin_are_accepted(tmp_path, monkeypatch) -> None:
    origin = "https://row-bot.example"
    client, _device_id = _phone(tmp_path, monkeypatch, origin)

    refused = _subscribe(client, origin, endpoint="https://collector.example/hook")
    assert refused.status_code == 400
    assert refused.json()["error"] == "invalid_push_subscription"
    assert _subscribe(client, "https://elsewhere.example").status_code == 403
    assert push.PushStore(tmp_path / "data").subscriptions() == []


def test_notices_show_details_until_the_person_turns_them_off(tmp_path, monkeypatch) -> None:
    origin = "https://row-bot.example"
    client, _device_id = _phone(tmp_path, monkeypatch, origin)

    assert client.get("/api/access/devices/push").json()["show_details"] is True

    off = client.post("/api/access/devices/push/details", json={"show_details": False}, headers={"origin": origin})
    assert off.json() == {"ok": True, "show_details": False}
    assert client.get("/api/access/devices/push").json()["show_details"] is False
    assert push.PushStore(tmp_path / "data").show_details() is False

    assert client.post("/api/access/devices/push/details", json={"show_details": "no"},
                       headers={"origin": origin}).status_code == 400
    assert client.post("/api/access/devices/push/details", json={"show_details": True},
                       headers={"origin": "https://elsewhere.example"}).status_code == 403
    assert push.PushStore(tmp_path / "data").show_details() is False
