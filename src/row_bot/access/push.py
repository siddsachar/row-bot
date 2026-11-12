"""Opt-in, content-free Web Push to a person's phones (D8).

A device that turned notifications on in Settings › Devices gets an empty,
encrypted wake-up whenever Row-Bot starts waiting on the person: an approval
or a question. The push service of that device's browser (Google, Apple,
Mozilla or Microsoft) carries the wake-up and sees no content; the device then
shows Row-Bot's own fixed words and opens Row-Bot when tapped.

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
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlsplit

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

VAPID_KEY_FILENAME = "push_vapid_key.pem"
SUBSCRIPTIONS_FILENAME = "push_subscriptions.json"
# The browsers' own push services. A subscription naming any other host is
# refused, so this server only ever sends to them.
PUSH_SERVICE_HOSTS = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    ".push.apple.com",
    ".notify.windows.com",
)
# A wake-up older than this is stale: the person has likely looked already.
WAKE_TTL_SECONDS = 15 * 60
_MAX_SUBSCRIPTIONS = 64
_lock = threading.Lock()


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


def _send(subscription: PushSubscription, key_path: Path) -> int | None:
    """Send one empty wake-up; the push service's status when it refused."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            {
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=None,
            vapid_private_key=str(key_path),
            vapid_claims={"sub": subscription.origin},
            ttl=WAKE_TTL_SECONDS,
            headers={"Urgency": "high", "Topic": "needs-you"},
            timeout=10,
        )
    except WebPushException as exc:
        response: Any = getattr(exc, "response", None)
        return int(getattr(response, "status_code", 0) or 0)
    return None


def wake(
    store: PushStore | None = None,
    *,
    active_devices: Callable[[], set[str]] = _active_device_ids,
    send: Callable[[PushSubscription, Path], int | None] = _send,
) -> int:
    """Wake every subscribed, still signed-in device; return how many."""
    store = store or PushStore()
    subscriptions = store.subscriptions()
    if not subscriptions:
        return 0
    active = active_devices()
    gone = {item.device_id for item in subscriptions if item.device_id not in active}
    sent = 0
    for item in subscriptions:
        if item.device_id in gone:
            continue
        try:
            refused = send(item, store.key_path)
        except Exception:  # noqa: BLE001 - one device's failure never stops the rest
            logger.warning("A push wake-up could not be sent.", exc_info=True)
            continue
        if refused in (404, 410):
            # The browser dropped this subscription: forget it.
            gone.add(item.device_id)
        elif refused is None:
            sent += 1
    if gone:
        store.keep(active - gone)
    return sent


def wake_soon() -> None:
    """Wake subscribed devices in the background; never delays the caller."""
    if not PushStore().subscriptions_path.exists():
        return
    threading.Thread(target=_wake_quietly, name="row-bot-push", daemon=True).start()


def _wake_quietly() -> None:
    try:
        wake()
    except Exception:  # noqa: BLE001 - a wake-up is best effort
        logger.warning("Push wake-ups failed.", exc_info=True)
