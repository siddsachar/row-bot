"""Opt-in Web Push notices to a person's phones (D8).

A device that turned notifications on in Settings › Devices gets a short
notice whenever Row-Bot starts waiting on the person (an approval or a
question) or has a moment for them: a title and one line saying what it's
about, such as "Approval needed" and "Send email to Sam?". With **Show
details in notifications** off (Settings › Devices) every notice says only
"Row-Bot is waiting for you".

Each notice is encrypted for that one browser (RFC 8291, aes128gcm) and
signed for its push service (VAPID, RFC 8292): the push service (Google,
Apple, Mozilla or Microsoft) carries ciphertext it can't read. The device
decrypts it, shows it, and opens Row-Bot at the right place when tapped.

Push needs a secure, reachable origin: it is offered only to a device signed
in over HTTPS (Tailscale, a tunnel or a hosted Row-Bot). Nothing here runs,
and no file is created, until a device turns it on.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

from row_bot.data_paths import get_row_bot_data_dir

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey

logger = logging.getLogger(__name__)

VAPID_KEY_FILENAME = "push_vapid_key.pem"
SUBSCRIPTIONS_FILENAME = "push_subscriptions.json"
SETTINGS_FILENAME = "push_settings.json"
# The browsers' own push services. A subscription naming any other host is
# refused, so this server only ever sends to them.
PUSH_SERVICE_HOSTS = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    ".push.apple.com",
    ".notify.windows.com",
)
# A notice older than this is stale: the person has likely looked already.
WAKE_TTL_SECONDS = 15 * 60
# What every notice says with details off, and the service worker's own
# words for a push that carries nothing.
GENERIC_TITLE = "Row-Bot"
GENERIC_BODY = "Row-Bot is waiting for you."
TITLE_CHARS = 60
BODY_CHARS = 120
APP_PATH = "/app-v2/"
# One aes128gcm record (RFC 8188); a notice is far smaller.
_RECORD_SIZE = 4096
_MAX_SUBSCRIPTIONS = 64
_lock = threading.Lock()


@dataclass(frozen=True)
class Notice:
    """What one push says: a title, one line, and where tapping it opens."""

    title: str
    body: str = ""
    path: str = APP_PATH


def _plain(text: str, limit: int) -> str:
    """One line of plain text of at most *limit* characters, secrets removed."""
    from row_bot.evolution import redact_secrets

    words = " ".join(redact_secrets(str(text or "")).split())
    for mark in ("**", "__", "`"):
        words = words.replace(mark, "")
    words = "".join(character for character in words if character.isprintable())
    return words if len(words) <= limit else words[: limit - 1].rstrip() + "…"


def notice(title: str, body: str = "", *, conversation_id: str = "") -> Notice:
    """A bounded notice; tapping it opens *conversation_id*, else Row-Bot's home."""
    path = f"{APP_PATH}conversations/{quote(conversation_id, safe='')}" if conversation_id else APP_PATH
    return Notice(_plain(title, TITLE_CHARS) or GENERIC_TITLE, _plain(body, BODY_CHARS), path)


@dataclass(frozen=True)
class PushSubscription:
    device_id: str
    endpoint: str
    p256dh: str
    auth: str
    # The HTTPS origin the device signed in on: the VAPID contact claim.
    origin: str


def endpoint_allowed(endpoint: str) -> bool:
    """Whether *endpoint* is an HTTPS address of a browser push service."""
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    return any(
        host.endswith(suffix) if suffix.startswith(".") else host == suffix
        for suffix in PUSH_SERVICE_HOSTS
    )


def push_origin_allowed(origin: str) -> bool:
    """Push is offered only on an HTTPS origin a phone can reach from away."""
    return urlsplit(origin or "").scheme == "https"


class PushStore:
    """The VAPID key and the subscribed devices, in the data folder."""

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder or get_row_bot_data_dir(create=False)

    @property
    def key_path(self) -> Path:
        return self.folder / VAPID_KEY_FILENAME

    @property
    def subscriptions_path(self) -> Path:
        return self.folder / SUBSCRIPTIONS_FILENAME

    @property
    def settings_path(self) -> Path:
        return self.folder / SETTINGS_FILENAME

    def show_details(self) -> bool:
        """Whether notices say what they're about (on unless turned off)."""
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return True
        except (OSError, UnicodeError, json.JSONDecodeError):
            # Unreadable: say nothing beyond the generic words until it's set again.
            logger.warning("Push settings could not be read; notices show no details.")
            return False
        return not (isinstance(saved, dict) and saved.get("show_details") is False)

    def set_show_details(self, on: bool) -> None:
        with _lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"show_details": bool(on)}), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.settings_path)

    def public_key(self) -> str:
        """The application server key a browser subscribes with (made once)."""
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )
        from py_vapid import Vapid01

        with _lock:
            if self.key_path.exists():
                vapid = Vapid01.from_file(str(self.key_path))
            else:
                self.folder.mkdir(parents=True, exist_ok=True)
                vapid = Vapid01()
                vapid.generate_keys()
                vapid.save_key(str(self.key_path))
                self.key_path.chmod(0o600)
        raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def subscriptions(self) -> list[PushSubscription]:
        try:
            rows = json.loads(self.subscriptions_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, json.JSONDecodeError):
            logger.warning("Push subscriptions could not be read; none are used.")
            return []
        found: list[PushSubscription] = []
        for row in rows if isinstance(rows, list) else []:
            try:
                found.append(PushSubscription(**{key: str(row[key]) for key in PushSubscription.__dataclass_fields__}))
            except (KeyError, TypeError):
                continue
        return found

    def _save(self, subscriptions: Iterable[PushSubscription]) -> None:
        rows = [asdict(item) for item in subscriptions]
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.subscriptions_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.subscriptions_path)

    def subscribed(self, device_id: str) -> bool:
        return any(item.device_id == device_id for item in self.subscriptions())

    def subscribe(self, subscription: PushSubscription) -> None:
        if not endpoint_allowed(subscription.endpoint):
            raise ValueError("not a browser push service")
        with _lock:
            kept = [
                item
                for item in self.subscriptions()
                if item.device_id != subscription.device_id
                and item.endpoint != subscription.endpoint
            ]
            self._save([*kept[-(_MAX_SUBSCRIPTIONS - 1):], subscription])

    def unsubscribe(self, device_id: str) -> None:
        with _lock:
            current = self.subscriptions()
            kept = [item for item in current if item.device_id != device_id]
            if len(kept) != len(current):
                self._save(kept)

    def keep(self, device_ids: set[str]) -> None:
        with _lock:
            current = self.subscriptions()
            kept = [item for item in current if item.device_id in device_ids]
            if len(kept) != len(current):
                self._save(kept)


def _active_device_ids() -> set[str]:
    from row_bot.access.store import AccessStore

    return {device.id for device in AccessStore().list_devices(include_revoked=False)}


def _b64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encrypt(
    plaintext: bytes,
    p256dh: str,
    auth: str,
    *,
    salt: bytes | None = None,
    server_key: EllipticCurvePrivateKey | None = None,
) -> bytes:
    """*plaintext* encrypted for one browser: an aes128gcm body (RFC 8291).

    A fresh key pair and salt per message (*salt* and *server_key* are fixed
    only by tests); only that browser, holding the subscription's private key
    and auth secret, can decrypt it.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    if len(plaintext) + 17 > _RECORD_SIZE:
        raise ValueError("a push notice is too long for one record")
    browser_public = _b64(p256dh)
    auth_secret = _b64(auth)
    private = server_key or ec.generate_private_key(ec.SECP256R1())
    server_public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    shared = private.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), browser_public)
    )
    salt = salt or os.urandom(16)
    info = b"WebPush: info\x00" + browser_public + server_public
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, info).derive(shared)
    key = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    # One record, so it is the last: its delimiter is 0x02, with no padding.
    body = AESGCM(key).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + _RECORD_SIZE.to_bytes(4, "big") + bytes([len(server_public)]) + server_public
    return header + body


def _payload(notice: Notice, show_details: bool) -> bytes:
    """What the service worker shows; with details off, only the generic words."""
    if show_details:
        words = {"title": notice.title, "body": notice.body}
    else:
        words = {"title": GENERIC_TITLE, "body": GENERIC_BODY}
    return json.dumps({**words, "path": notice.path}, ensure_ascii=False).encode("utf-8")


def _send(subscription: PushSubscription, key_path: Path, payload: bytes) -> int | None:
    """Send one encrypted notice; the push service's status when it refused.

    Encrypted for that browser (RFC 8291) and signed for its push service
    (VAPID, RFC 8292): the push service sees only ciphertext.
    """
    import time

    import httpx
    from py_vapid import Vapid02

    endpoint = urlsplit(subscription.endpoint)
    claims = {
        "aud": f"{endpoint.scheme}://{endpoint.netloc}",
        "sub": subscription.origin,
        # Push services take a signature for at most a day.
        "exp": int(time.time()) + 12 * 60 * 60,
    }
    headers = {
        **Vapid02.from_file(str(key_path)).sign(claims),
        "TTL": str(WAKE_TTL_SECONDS),
        "Urgency": "high",
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
    }
    content = encrypt(payload, subscription.p256dh, subscription.auth)
    response = httpx.post(subscription.endpoint, headers=headers, content=content, timeout=10)
    return None if 200 <= response.status_code < 300 else response.status_code


def notify(
    notice: Notice,
    store: PushStore | None = None,
    *,
    active_devices: Callable[[], set[str]] = _active_device_ids,
    send: Callable[[PushSubscription, Path, bytes], int | None] = _send,
) -> int:
    """Send *notice* to every subscribed, still signed-in device; return how many."""
    store = store or PushStore()
    subscriptions = store.subscriptions()
    if not subscriptions:
        return 0
    active = active_devices()
    gone = {item.device_id for item in subscriptions if item.device_id not in active}
    payload = _payload(notice, store.show_details())
    sent = 0
    for item in subscriptions:
        if item.device_id in gone:
            continue
        try:
            refused = send(item, store.key_path, payload)
        except Exception:  # noqa: BLE001 - one device's failure never stops the rest
            logger.warning("A push notice could not be sent.", exc_info=True)
            continue
        if refused in (404, 410):
            # The browser dropped this subscription: forget it.
            gone.add(item.device_id)
        elif refused is None:
            sent += 1
    if gone:
        store.keep(active - gone)
    return sent


def notify_soon(title: str, body: str = "", *, conversation_id: str = "") -> None:
    """Tell subscribed devices in the background; never delays the caller.

    *title* (up to 60 characters) and *body* (one line, up to 120) are cut to
    length as plain text, with secrets removed; tapping the notice opens
    *conversation_id*, or Row-Bot's home without one. With details off the
    devices show only "Row-Bot is waiting for you".
    """
    if not PushStore().subscriptions_path.exists():
        return
    item = notice(title, body, conversation_id=conversation_id)
    threading.Thread(target=_notify_quietly, args=(item,), name="row-bot-push", daemon=True).start()


def _notify_quietly(item: Notice) -> None:
    try:
        notify(item)
    except Exception:  # noqa: BLE001 - a notice is best effort
        logger.warning("Push notices failed.", exc_info=True)
