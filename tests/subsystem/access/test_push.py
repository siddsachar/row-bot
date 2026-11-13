"""Opt-in, content-free Web Push (D8): what is sent, to whom, and when not."""

from __future__ import annotations

import base64
import json

import pytest

from row_bot.access import push


def _subscription(device: str, endpoint: str = "https://fcm.googleapis.com/fcm/send/abc") -> push.PushSubscription:
    return push.PushSubscription(
        device_id=device,
        endpoint=endpoint,
        p256dh="BPublicKeyOfThatBrowser",
        auth="secret-auth",
        origin="https://row-bot.example",
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/abc",
        "https://updates.push.services.mozilla.com/wpush/v2/abc",
        "https://web.push.apple.com/QGx1",
        "https://db5p.notify.windows.com/w/?token=abc",
    ],
)
def test_browser_push_services_are_allowed(endpoint: str) -> None:
    assert push.endpoint_allowed(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/abc",
        "https://evil.example/fcm.googleapis.com",
        "https://fcm.googleapis.com.evil.example/x",
        "https://user:pass@fcm.googleapis.com/x",
        "https://apple.com.evil.example/x",
        "not a url",
    ],
)
def test_any_other_address_is_refused_so_the_server_never_posts_elsewhere(endpoint: str) -> None:
    assert not push.endpoint_allowed(endpoint)
    with pytest.raises(ValueError):
        push.PushStore().subscribe(_subscription("device-a", endpoint))


def test_only_https_origins_offer_push() -> None:
    assert push.push_origin_allowed("https://row-bot.example")
    assert not push.push_origin_allowed("http://192.168.1.20:8080")
    assert not push.push_origin_allowed("")


def test_the_server_key_is_made_once_and_kept(tmp_path) -> None:
    store = push.PushStore(tmp_path)
    first = store.public_key()
    raw = base64.urlsafe_b64decode(first + "=" * (-len(first) % 4))
    # An uncompressed P-256 point, as browsers expect for applicationServerKey.
    assert len(raw) == 65 and raw[0] == 4
    assert push.PushStore(tmp_path).public_key() == first


def test_a_device_has_one_subscription_and_can_turn_it_off(tmp_path) -> None:
    store = push.PushStore(tmp_path)
    store.subscribe(_subscription("device-a", "https://fcm.googleapis.com/fcm/send/old"))
    store.subscribe(_subscription("device-a", "https://fcm.googleapis.com/fcm/send/new"))
    store.subscribe(_subscription("device-b"))
    assert [item.endpoint for item in store.subscriptions() if item.device_id == "device-a"] == [
        "https://fcm.googleapis.com/fcm/send/new"
    ]
    store.unsubscribe("device-a")
    assert not store.subscribed("device-a")
    assert store.subscribed("device-b")


def test_nothing_runs_and_no_file_appears_until_a_device_turns_push_on(tmp_path) -> None:
    store = push.PushStore(tmp_path)
    sent = []
    assert push.wake(store, active_devices=lambda: {"device-a"}, send=lambda *args: sent.append(args)) == 0
    assert sent == []
    assert list(tmp_path.iterdir()) == []


def test_a_wake_up_reaches_signed_in_devices_and_forgets_gone_ones(tmp_path) -> None:
    store = push.PushStore(tmp_path)
    for device in ("signed-in", "refused-by-browser", "signed-out"):
        store.subscribe(_subscription(device, f"https://fcm.googleapis.com/fcm/send/{device}"))
    reached = []

    def send(subscription, _key_path):
        reached.append(subscription.device_id)
        return 410 if subscription.device_id == "refused-by-browser" else None

    sent = push.wake(store, active_devices=lambda: {"signed-in", "refused-by-browser"}, send=send)

    assert sent == 1
    assert reached == ["signed-in", "refused-by-browser"]
    assert [item.device_id for item in store.subscriptions()] == ["signed-in"]


def test_the_wake_up_carries_no_content(tmp_path, monkeypatch) -> None:
    import httpx

    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: calls.append((url, kwargs)) or httpx.Response(201))
    store = push.PushStore(tmp_path)
    store.public_key()

    assert push._send(_subscription("device-a"), store.key_path) is None

    url, options = calls[0]
    assert url == "https://fcm.googleapis.com/fcm/send/abc"
    assert options["content"] == b""
    headers = options["headers"]
    assert headers["Topic"] == "needs-you" and headers["TTL"] == str(push.WAKE_TTL_SECONDS)
    # Signed for this push service with Row-Bot's own key (VAPID, RFC 8292).
    scheme, _, fields = headers["Authorization"].partition(" ")
    token = dict(field.split("=", 1) for field in fields.split(","))["t"]
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    assert scheme == "vapid"
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["sub"] == "https://row-bot.example"
    assert dict(field.split("=", 1) for field in fields.split(","))["k"] == store.public_key()
    from py_vapid import Vapid02

    assert Vapid02.verify(headers["Authorization"])


def test_a_push_service_refusal_reports_its_status(tmp_path, monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: httpx.Response(410))
    store = push.PushStore(tmp_path)
    store.public_key()

    assert push._send(_subscription("device-a"), store.key_path) == 410

