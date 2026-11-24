"""The one HTTP client to a joined company's Row-Bot server.

- One origin, https only (``http://127.0.0.1`` or ``localhost`` for development and tests).
- No cookie jar: each request uses a fresh connection, so nothing the server sets is ever sent back (the server
  refuses a bearer that comes with its session cookie). Each call carries the device's bearer.
- The access token is refreshed when it is about to end, and once more on a 401. Refreshes run one at a time,
  and the new pair is saved in the keychain before it is used (the old refresh token stops working at once).
- A refresh refused with ``invalid_grant`` means the company disconnected this Row-Bot: revoked, suspended or a
  month since the last sign-in. The record says so and the credential is removed.
- 429 means wait: nothing is sent until ``Retry-After`` has passed, and the credential is kept.

Nothing here logs a token, a code or a response body.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from row_bot.company import store

logger = logging.getLogger(__name__)

TIMEOUT = 15.0
TOKEN_PATH = "/api/device/token"
# Refresh this many seconds before the access token ends, so a call never races its end.
EARLY = 60
DEFAULT_RETRY_AFTER = 60
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})

_REFRESH = threading.Lock()
_backoff_until = 0.0
# Seconds since the epoch; tests move it.
now = time.time
# Where requests go: None is the network; tests put a fake Company Server here.
transport: httpx.BaseTransport | None = None


class InvalidAddress(ValueError):
    """Not an address Row-Bot joins: ``not_https`` or ``invalid_address``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Unreachable(Exception):
    """The server couldn't be reached, or answered with something unexpected; the credential is kept."""


class Busy(Unreachable):
    """The server said to come back later (429)."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"retry after {retry_after} s")
        self.retry_after = retry_after


class NotCompanyServer(Exception):
    """The address answers, but not as a Row-Bot company server."""


class Refused(Exception):
    """The token endpoint refused a code or refresh token (its error code, never the token)."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


class Disconnected(Exception):
    """The company no longer accepts this Row-Bot."""


@dataclass(frozen=True)
class Grant:
    credential: store.Credential
    device_id: str


def normalise_origin(address: str) -> str:
    """The server's origin from what the person typed: ``https://host[:port]``, lower case, no path.

    A bare host means https. ``http://`` is refused except on this computer's loopback (development)."""
    text = address.strip()
    if not text or len(text) > 512 or any(ch.isspace() or ch == "\\" or ord(ch) < 32 for ch in text):
        raise InvalidAddress("invalid_address")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise InvalidAddress("invalid_address") from None
    scheme = parts.scheme.lower()
    if (not host or parts.username is not None or parts.password is not None or parts.query or parts.fragment
            or parts.path not in ("", "/")):
        raise InvalidAddress("invalid_address")
    if scheme == "http" and host not in LOOPBACK_HOSTS:
        raise InvalidAddress("not_https")
    if scheme not in ("http", "https"):
        raise InvalidAddress("invalid_address")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if scheme == "https" else 80):
        netloc += f":{port}"
    return f"{scheme}://{netloc}"


def _request(origin: str, method: str, path: str, *, bearer: str | None = None,
             body: dict[str, str] | None = None, extra: dict[str, str] | None = None,
             content: bytes | None = None, files: dict[str, Any] | None = None,
             data: dict[str, str] | None = None, timeout: float | None = None) -> httpx.Response:
    global _backoff_until
    wait = int(_backoff_until - now())
    if wait > 0:
        raise Busy(wait)
    headers = {"Accept": "application/json", **(extra or {})}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if content is not None:
        headers["Content-Type"] = "application/json"
    try:
        # A client per request: no cookie the server sets is ever sent back.
        with httpx.Client(base_url=origin, timeout=TIMEOUT if timeout is None else timeout, follow_redirects=False,
                          transport=transport) as http:
            response = http.request(method, path, headers=headers, json=body, content=content, files=files,
                                    data=data)
    except httpx.HTTPError as error:
        logger.info("The company server couldn't be reached (%s)", type(error).__name__)
        raise Unreachable(type(error).__name__) from None
    if response.status_code == 429:
        try:
            retry_after = min(max(int(response.headers.get("Retry-After", "")), 1), 600)
        except ValueError:
            retry_after = DEFAULT_RETRY_AFTER
        _backoff_until = now() + retry_after
        logger.info("The company server asked Row-Bot to wait %d s", retry_after)
        raise Busy(retry_after)
    if response.status_code >= 500:
        logger.info("The company server answered %d", response.status_code)
        raise Unreachable(str(response.status_code))
    return response


def _answer(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def check_server(origin: str) -> None:
    """Ask ``/healthz`` only: whether the address is a Row-Bot company server, before the browser opens."""
    response = _request(origin, "GET", "/healthz")
    if response.status_code != 200 or _answer(response).get("status") != "ok":
        raise NotCompanyServer(origin)


def _grant(response: httpx.Response) -> Grant:
    data = _answer(response)
    if response.status_code == 400:
        raise Refused(str(data.get("error") or "invalid_request")[:64])
    access, refresh, device = data.get("access_token"), data.get("refresh_token"), data.get("device_id")
    lifetime = data.get("expires_in")
    if (response.status_code != 200 or data.get("token_type") != "Bearer"
            or not all(isinstance(value, str) and value for value in (access, refresh, device))
            or not isinstance(lifetime, int) or isinstance(lifetime, bool) or lifetime <= 0):
        raise Unreachable("unexpected token answer")
    return Grant(store.Credential(access, refresh, now() + lifetime), device)


def exchange_code(origin: str, *, code: str, verifier: str, redirect_uri: str) -> Grant:
    """The sign-in's one-time code for the device's first pair (the code works once)."""
    return _grant(_request(origin, "POST", TOKEN_PATH, body={
        "grant_type": "authorization_code", "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri,
    }))


def disconnect() -> None:
    """The company no longer accepts this Row-Bot: the record says so and the credential goes."""
    from row_bot.application.live_updates import publish

    logger.info("The company no longer accepts this Row-Bot; it shows as disconnected")
    store.mark_disconnected()
    store.delete_credential()
    publish("models")  # Its models show as unavailable now.


def _refresh(origin: str, stale: store.Credential) -> store.Credential:
    """A new pair for ``stale``, saved before it is returned; one refresh at a time.

    A call that waited while another refreshed uses that call's pair. An answer lost on the way is asked for
    once more at once: the server gives it again within a minute."""
    with _REFRESH:
        current = store.read_credential()
        if current is None:
            raise Disconnected
        if current.refresh_token != stale.refresh_token:
            return current
        for attempt in (1, 2):
            try:
                grant = _grant(_request(origin, "POST", TOKEN_PATH, body={
                    "grant_type": "refresh_token", "refresh_token": current.refresh_token,
                }))
                break
            except Busy:
                raise
            except Unreachable:
                if attempt == 2:
                    raise
            except Refused as refused:
                if refused.error == "invalid_grant":
                    disconnect()
                    raise Disconnected from None
                raise Unreachable(refused.error) from None
        store.save_credential(grant.credential)  # Before the new pair is used: the old one is spent.
        return grant.credential


def bearer(origin: str) -> store.Credential:
    """The credential to present now, refreshed first when its access token is about to end.

    Raises Disconnected, Unreachable (Busy for a 429) or store.StorageUnavailable (a locked keychain)."""
    credential = store.read_credential()
    if credential is None:
        disconnect()
        raise Disconnected
    if credential.expires_at - EARLY <= now():
        credential = _refresh(origin, credential)
    return credential


def refreshed(origin: str, stale: store.Credential) -> store.Credential:
    """After a 401: a new pair (or the one another call has just saved). Raises as ``bearer``."""
    return _refresh(origin, stale)


def call(origin: str, method: str, path: str, *, headers: dict[str, str] | None = None,
         content: bytes | None = None, files: dict[str, Any] | None = None, data: dict[str, str] | None = None,
         timeout: float | None = None) -> httpx.Response:
    """A call with the device's bearer, refreshing it when it is about to end and once more on a 401.
    ``headers`` are sent too (``If-None-Match``, say); ``content`` is a JSON body, sent as given; ``files`` and
    ``data`` a ``multipart/form-data`` body (a company document's upload). ``timeout`` (seconds) replaces
    ``TIMEOUT`` for this call's requests.

    Raises Disconnected, Unreachable (Busy for a 429) or store.StorageUnavailable (a locked keychain)."""
    credential = bearer(origin)
    sent = {"extra": headers, "content": content, "files": files, "data": data, "timeout": timeout}
    response = _request(origin, method, path, bearer=credential.access_token, **sent)
    if response.status_code == 401 and _answer(response).get("error") == "invalid_token":
        credential = refreshed(origin, credential)
        response = _request(origin, method, path, bearer=credential.access_token, **sent)
        if response.status_code == 401:
            disconnect()
            raise Disconnected
    return response


def me(origin: str) -> tuple[str, str]:
    """The signed-in person's name and email (``/api/me``)."""
    response = call(origin, "GET", "/api/me")
    data = _answer(response)
    if response.status_code != 200:
        raise Unreachable(str(response.status_code))
    return str(data.get("display_name") or "")[:200], str(data.get("email") or "")[:320]


def leave(origin: str) -> None:
    """Tell the server this device leaves (it revokes the device and its tokens)."""
    response = call(origin, "POST", "/api/device/leave")
    if response.status_code not in (200, 204):
        raise Unreachable(str(response.status_code))
