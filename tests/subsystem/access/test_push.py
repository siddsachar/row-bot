"""Opt-in Web Push notices (D8): what is sent, how it is sealed, to whom, and when not."""

from __future__ import annotations

import base64
import json
import os

import pytest

from row_bot.access import push


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _subscription(device: str, endpoint: str = "https://fcm.googleapis.com/fcm/send/abc") -> push.PushSubscription:
    return push.PushSubscription(
        device_id=device,
        endpoint=endpoint,
        p256dh="BPublicKeyOfThatBrowser",
        auth="secret-auth",
        origin="https://row-bot.example",
    )


def _browser():
    """A browser's own subscription keys: its P-256 key pair and auth secret."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return private, public, os.urandom(16)


def _decrypt(body: bytes, private, public: bytes, auth: bytes) -> bytes:
    """What the browser does with an aes128gcm push body (RFC 8188 and RFC 8291)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    salt, record_size, key_length = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    server_public, ciphertext = body[21 : 21 + key_length], body[21 + key_length :]
    assert record_size >= len(ciphertext)
    shared = private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_public))
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + public + server_public).derive(shared)
    key = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    record = AESGCM(key).decrypt(nonce, ciphertext, None)
    assert record.endswith(b"\x02")  # the last (and only) record, unpadded
    return record[:-1]


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
    raw = _unb64(first)
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
    notice = push.notice("Approval needed", "Send email to Sam?")
    assert push.notify(notice, store, active_devices=lambda: {"device-a"}, send=lambda *args: sent.append(args)) == 0
    assert sent == []
    assert list(tmp_path.iterdir()) == []


def test_a_notice_reaches_signed_in_devices_and_forgets_gone_ones(tmp_path) -> None:
    store = push.PushStore(tmp_path)
    for device in ("signed-in", "refused-by-browser", "signed-out"):
        store.subscribe(_subscription(device, f"https://fcm.googleapis.com/fcm/send/{device}"))
    reached = []

    def send(subscription, _key_path, _payload):
        reached.append(subscription.device_id)
        return 410 if subscription.device_id == "refused-by-browser" else None

    sent = push.notify(
        push.notice("Approval needed"), store, active_devices=lambda: {"signed-in", "refused-by-browser"}, send=send
    )

    assert sent == 1
    assert reached == ["signed-in", "refused-by-browser"]
    assert [item.device_id for item in store.subscriptions()] == ["signed-in"]


def test_encryption_matches_the_web_push_standard_example() -> None:
    """RFC 8291 Appendix A: the same keys and salt give the standard's exact message."""
    from cryptography.hazmat.primitives.asymmetric import ec

    server_key = ec.derive_private_key(
        int.from_bytes(_unb64("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1()
    )
    body = push.encrypt(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg",
        salt=_unb64("DGv6ra1nlYgDCS1FRnbzlw"),
        server_key=server_key,
    )
    assert _b64(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6Tlz"
        "AC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


def _sent_body(tmp_path, monkeypatch, notice: push.Notice, *, show_details: bool | None = None):
    """Send *notice* to one browser through the real path; what went over the wire, and that browser."""
    import httpx

    private, public, auth = _browser()
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: calls.append((url, kwargs)) or httpx.Response(201))
    store = push.PushStore(tmp_path)
    store.public_key()
    if show_details is not None:
        store.set_show_details(show_details)
    subscription = push.PushSubscription(
        device_id="device-a",
        endpoint="https://fcm.googleapis.com/fcm/send/abc",
        p256dh=_b64(public),
        auth=_b64(auth),
        origin="https://row-bot.example",
    )
    store.subscribe(subscription)

    assert push.notify(notice, store, active_devices=lambda: {"device-a"}) == 1

    [(url, options)] = calls
    assert url == subscription.endpoint
    return options, (private, public, auth), store


def test_the_notice_is_sealed_for_that_browser_and_says_what_it_is_about(tmp_path, monkeypatch) -> None:
    notice = push.notice("Approval needed", "Send email to Sam?", conversation_id="chat-1")
    options, browser, store = _sent_body(tmp_path, monkeypatch, notice)

    body = options["content"]
    # The push service sees only ciphertext: none of the words travel in the clear.
    for words in (b"Approval needed", b"Send email to Sam", b"chat-1", b"title"):
        assert words not in body
    assert json.loads(_decrypt(body, *browser)) == {
        "title": "Approval needed",
        "body": "Send email to Sam?",
        "path": "/app-v2/conversations/chat-1",
    }
    headers = options["headers"]
    assert headers["Content-Encoding"] == "aes128gcm"
    assert headers["TTL"] == str(push.WAKE_TTL_SECONDS)
    # Signed for this push service with Row-Bot's own key (VAPID, RFC 8292).
    scheme, _, fields = headers["Authorization"].partition(" ")
    signed = dict(field.split("=", 1) for field in fields.split(","))
    claims = json.loads(_unb64(signed["t"].split(".")[1]))
    assert scheme == "vapid"
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["sub"] == "https://row-bot.example"
    assert signed["k"] == store.public_key()
    from py_vapid import Vapid02

    assert Vapid02.verify(headers["Authorization"])


def test_each_notice_is_sealed_afresh() -> None:
    private, public, auth = _browser()
    first = push.encrypt(b"same words", _b64(public), _b64(auth))
    second = push.encrypt(b"same words", _b64(public), _b64(auth))
    assert first != second
    assert _decrypt(first, private, public, auth) == _decrypt(second, private, public, auth) == b"same words"


def test_with_details_off_a_notice_carries_only_the_generic_words(tmp_path, monkeypatch) -> None:
    notice = push.notice("Row-Bot asked you a question", "Which venue?", conversation_id="chat-1")
    options, browser, _store = _sent_body(tmp_path, monkeypatch, notice, show_details=False)

    sent = json.loads(_decrypt(options["content"], *browser))
    assert sent["title"] == push.GENERIC_TITLE == "Row-Bot"
    assert sent["body"] == push.GENERIC_BODY == "Row-Bot is waiting for you."
    assert "venue" not in json.dumps(sent).lower() and "question" not in json.dumps(sent).lower()


def test_details_are_shown_unless_turned_off_and_an_unreadable_setting_shows_none(tmp_path) -> None:
    store = push.PushStore(tmp_path)
    assert store.show_details() is True
    store.set_show_details(False)
    assert push.PushStore(tmp_path).show_details() is False
    store.set_show_details(True)
    assert store.show_details() is True
    store.settings_path.write_text("{not json", encoding="utf-8")
    assert store.show_details() is False


def test_a_notice_is_short_plain_text_without_secrets() -> None:
    notice = push.notice(
        "Approval needed for **a very long title** " + "x" * 200,
        "Run `deploy`\nwith token=abcdef1234567890 and sk-abcdefghijklmnopqrstuvwxyz " + "y" * 300,
        conversation_id="chat/../1",
    )
    assert len(notice.title) == push.TITLE_CHARS and notice.title.endswith("…")
    assert len(notice.body) == push.BODY_CHARS
    assert "**" not in notice.title and "`" not in notice.body and "\n" not in notice.body
    assert "abcdef1234567890" not in notice.body and "sk-abcdefghij" not in notice.body
    # A conversation id is one path segment: it can't lead elsewhere in the app.
    assert notice.path == "/app-v2/conversations/chat%2F..%2F1"
    assert push.notice("", "").title == push.GENERIC_TITLE
    assert push.notice("Workflow finished", "Morning lines").path == "/app-v2/"


def test_a_push_service_refusal_reports_its_status(tmp_path, monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: httpx.Response(410))
    store = push.PushStore(tmp_path)
    store.public_key()
    _private, public, auth = _browser()
    subscription = push.PushSubscription("device-a", "https://fcm.googleapis.com/fcm/send/abc",
                                         _b64(public), _b64(auth), "https://row-bot.example")

    assert push._send(subscription, store.key_path, b"{}") == 410
