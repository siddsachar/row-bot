"""Several X accounts, each named on everything Row-Bot does with it (N7).

Every connected X account signs in with the person's own X app and keeps its sign-in in its own
keychain slot, ``x:<user ID>`` (``account_tokens``). What isn't secret, its numeric ID, handle and
display name, is listed in ``x/accounts.json`` in the data folder, in the order the accounts were
added. Tokens never leave the keychain: they are never returned, logged or shown.

There is no "current account". A tool call names its account, and with more than one connected a
write that doesn't is refused with the list (``resolve``).

Before 5.3 there was one X sign-in, in the slot ``x``. ``migrate`` makes it the first account: its
handle and ID are read once from X (``/2/users/me``), the token is saved in its own slot and read
back, the account is listed, and only then is the old slot removed. Until then the old sign-in
stays readable; the next check, sign-in or tool call tries again.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from row_bot import account_tokens
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

API_BASE = "https://api.x.com/2"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
LEGACY_SLOT = "x"  # The one X sign-in before 5.3.
MAX_ACCOUNTS = 16
# A fake X API stands in here in tests (an httpx transport); None is the network.
transport: Any = None

_ID = re.compile(r"^[0-9]{1,20}$")
_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,50}$")
_LOCK = threading.RLock()
_REFRESH = threading.Lock()  # One refresh at a time: X's refresh tokens work once.


class XAccountError(RuntimeError):
    """X couldn't say who a sign-in is, or the keychain couldn't keep it; nothing was changed."""


class XAccountChoice(ValueError):
    """Which account a call means isn't clear; the message says so in plain words."""


@dataclass(frozen=True)
class XAccount:
    id: str
    username: str
    name: str = ""

    @property
    def slot(self) -> str:
        return f"x:{self.id}"

    @property
    def handle(self) -> str:
        return f"@{self.username}"

    def post_url(self, post_id: str) -> str:
        return f"https://x.com/{self.username}/status/{post_id}"


def send(method: str, url: str, *, timeout: float = 15, **kwargs: Any) -> Any:
    """One request to X (or, in tests, the fake X API)."""
    import httpx

    with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False) as client:
        return client.request(method, url, **kwargs)


def bearer(token: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {token.get('access_token', '')}"}


def expired(token: dict) -> bool:
    """Whether the access token has run out (with a minute to spare)."""
    expires_at = token.get("expires_at", 0)
    return not isinstance(expires_at, (int, float)) or time.time() >= expires_at - 60


def _app() -> tuple[str, str]:
    from row_bot.api_keys import get_key

    return get_key("X_CLIENT_ID") or "", get_key("X_CLIENT_SECRET") or ""


def refresh(token: dict) -> dict | None:
    """A fresh access token for this sign-in, or None (X refused it, or X couldn't be reached)."""
    refresh_token = token.get("refresh_token")
    client_id, client_secret = _app()
    if not refresh_token or not client_id:
        return None
    try:
        response = send("POST", TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": refresh_token,
                                                 "client_id": client_id}, auth=(client_id, client_secret))
        response.raise_for_status()
        fresh = response.json()
        fresh["expires_at"] = time.time() + float(fresh.get("expires_in", 7200))
    except Exception as exc:
        logger.warning("Refreshing an X sign-in failed (%s)", type(exc).__name__)
        return None
    return fresh if fresh.get("access_token") else None


def identify(token: dict) -> XAccount:
    """Who a sign-in is: X's ``/2/users/me``. Raises XAccountError when X doesn't say."""
    try:
        response = send("GET", f"{API_BASE}/users/me", headers=bearer(token),
                        params={"user.fields": "name,username"}, timeout=10)
    except Exception as exc:
        raise XAccountError(f"X couldn't be reached ({type(exc).__name__})") from None
    if response.status_code != 200:
        raise XAccountError(f"X answered {response.status_code}")
    try:
        data = response.json().get("data") or {}
    except ValueError:
        data = {}
    account = XAccount(str(data.get("id") or ""), str(data.get("username") or ""),
                       " ".join(str(data.get("name") or "").split())[:80])
    if not _ID.fullmatch(account.id) or not _HANDLE.fullmatch(account.username):
        raise XAccountError("X didn't say who this sign-in is")
    return account


# ── The list ─────────────────────────────────────────────────────────────────


def _path() -> Path:
    return get_row_bot_data_dir() / "x" / "accounts.json"


def listed() -> list[XAccount]:
    """The connected accounts, in the order they were added."""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found: list[XAccount] = []
    for item in raw.get("accounts", []) if isinstance(raw, dict) else []:
        if not isinstance(item, dict):
            continue
        account = XAccount(str(item.get("id") or ""), str(item.get("username") or ""), str(item.get("name") or "")[:80])
        if _ID.fullmatch(account.id) and _HANDLE.fullmatch(account.username) and account.id not in {
                known.id for known in found}:
            found.append(account)
    return found[:MAX_ACCOUNTS]


def _write(accounts: list[XAccount]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"accounts": [
        {"id": account.id, "username": account.username, "name": account.name} for account in accounts]},
        indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _keep(account: XAccount, *, first: bool = False) -> None:
    """List the account (its handle and name as X says them now), keeping its place."""
    accounts = listed()
    at = next((index for index, known in enumerate(accounts) if known.id == account.id), None)
    if at is not None:
        accounts[at] = account
    elif first:
        accounts.insert(0, account)
    else:
        accounts.append(account)
    _write(accounts[:MAX_ACCOUNTS])


def legacy_pending() -> bool:
    """Whether the sign-in kept before 5.3 is still waiting to become the first account."""
    return account_tokens.read(LEGACY_SLOT) is not None


def state(account: XAccount) -> str:
    """The account's sign-in state, from its last check (no X call)."""
    return account_tokens.state(account.slot)


def summaries() -> list[dict[str, str]]:
    """Each account's ID, handle, name and sign-in state, for Settings; never a token."""
    rows = [{"id": account.id, "username": account.username, "name": account.name,
             # Listed without a saved sign-in (removed from the keychain elsewhere): it needs signing in again.
             "state": "expired" if (found := state(account)) == "not_authenticated" else found}
            for account in listed()]
    if legacy_pending():
        # Its handle comes from X at the next check: until then it is "your X account".
        rows.insert(0, {"id": "", "username": "", "name": "", "state": account_tokens.state(LEGACY_SLOT)})
    return rows


def overall_state() -> str:
    """One state for X as a whole: any account needing a new sign-in comes first."""
    states = [row["state"] for row in summaries()]
    if not states:
        return "not_authenticated"
    for needs_sign_in in ("invalid", "expired"):
        if needs_sign_in in states:
            return needs_sign_in
    if all(item == "connected" for item in states):
        return "connected"
    return "unavailable" if all(item == "unavailable" for item in states) else "saved_unchecked"


def digest() -> str:
    """Identifies every saved X sign-in and the list together, without revealing them."""
    import hashlib

    parts = [account_tokens.digest(LEGACY_SLOT)]
    parts += [f"{account.id}:{account.username}:{account.name}:{account_tokens.digest(account.slot)}"
              for account in listed()]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest() if any(parts) else ""


# ── Changes ──────────────────────────────────────────────────────────────────


def migrate() -> XAccount | None:
    """Make the sign-in kept before 5.3 the first account; None when there is none to move.

    Raises XAccountError when X can't say who it is yet: the old sign-in stays where it was.
    """
    with _LOCK:
        token = account_tokens.read(LEGACY_SLOT)
        if token is None:
            return None
        if expired(token):
            with _REFRESH:
                fresh = refresh(token)
                if fresh is None:
                    raise XAccountError("the old X sign-in couldn't be renewed")
                try:  # X's refresh tokens work once: keep the new one before anything else can fail.
                    account_tokens.write(LEGACY_SLOT, fresh)
                except account_tokens.AccountTokenError:
                    raise XAccountError("the keychain couldn't keep the X sign-in") from None
                token = fresh
        account = identify(token)
        try:
            if account_tokens.read(account.slot) is None:  # A newer sign-in of the same account wins.
                account_tokens.write(account.slot, token)
                account_tokens.record_check(account.slot, "valid")
        except account_tokens.AccountTokenError:
            raise XAccountError("the keychain couldn't keep the X sign-in") from None
        _keep(account, first=True)
        try:
            account_tokens.delete(LEGACY_SLOT)  # Only now: the account's own slot holds it, read back.
        except account_tokens.AccountTokenError:
            logger.warning("The old X sign-in couldn't be removed yet; it is removed at the next check")
        logger.info("The X sign-in from before 5.3 is now the account %s", account.handle)
        return account


def add(token: dict) -> XAccount:
    """Save a new sign-in as its own account (or as a new sign-in of an account already listed)."""
    try:
        migrate()  # The sign-in kept before 5.3 comes first.
    except XAccountError:
        logger.info("The X sign-in from before 5.3 stays as it is until X can say who it is")
    account = identify(token)
    with _LOCK:
        known = {item.id for item in listed()}
        if account.id not in known and len(known) >= MAX_ACCOUNTS:
            raise XAccountError("too many X accounts")
        try:
            account_tokens.write(account.slot, token)
        except account_tokens.AccountTokenError:
            raise XAccountError("the keychain couldn't keep the X sign-in") from None
        account_tokens.record_check(account.slot, "valid")  # X has just issued it.
        _keep(account)
    return account


def rename(account: XAccount, username: str, name: str) -> None:
    """Follow a handle or display name changed on X (the ID never changes)."""
    renamed = XAccount(account.id, username, " ".join(name.split())[:80])
    if not _HANDLE.fullmatch(username):
        raise ValueError("not an X handle")
    with _LOCK:
        if any(item.id == account.id for item in listed()):
            _keep(renamed)


def remove(account_id: str) -> None:
    """Forget one account: its keychain slot and its place in the list; the others stay."""
    with _LOCK:
        if account_id == "":
            account_tokens.delete(LEGACY_SLOT)
            return
        if not _ID.fullmatch(account_id):
            raise XAccountChoice("No such X account.")
        account_tokens.delete(f"x:{account_id}")
        _write([account for account in listed() if account.id != account_id])


def token(account: XAccount) -> dict | None:
    return account_tokens.read(account.slot)


# What X calls the permission to upload pictures and videos. Row-Bot's sign-in asks for it from 5.3.1; an account
# connected before then has a sign-in without it, and X refuses its uploads.
MEDIA_SCOPE = "media.write"


def can_post_media(account: XAccount) -> bool:
    """Whether the account's sign-in lets Row-Bot upload pictures and videos (X's ``media.write``)."""
    return MEDIA_SCOPE in str((token(account) or {}).get("scope") or "").split()


def media_reconnect(account: XAccount) -> str:
    """What the person does, once, so pictures and videos can go out as this account."""
    return (f"{account.handle} was connected before Row-Bot could post pictures and videos. Sign in to it once "
            f"more in Settings › Apps › X (Sign in, then choose {account.handle} on X); text posts work as they are.")


def save_token(account: XAccount, value: dict) -> None:
    """Keep a refreshed sign-in in the account's slot; if the keychain can't, it serves this request only."""
    try:
        account_tokens.write(account.slot, value)
    except account_tokens.AccountTokenError:
        logger.error("The refreshed sign-in of %s couldn't be saved; it is used for this request only",
                     account.handle)


def valid_token(account: XAccount) -> dict | None:
    """The account's sign-in with a usable access token (refreshed once if it had run out), or None."""
    current = token(account)
    if current is None or not expired(current):
        return current
    with _REFRESH:
        current = token(account)  # Another request may have refreshed it meanwhile.
        if current is None or not expired(current):
            return current
        fresh = refresh(current)
        if fresh is not None:
            save_token(account, fresh)
        return fresh


# ── Which account a call means ───────────────────────────────────────────────


def names(accounts: list[XAccount]) -> str:
    """"@a", "@a or @b", "@a, @b or @c"."""
    handles = [account.handle for account in accounts]
    return handles[0] if len(handles) == 1 else f"{', '.join(handles[:-1])} or {handles[-1]}"


def resolve(account: str | None, *, required: bool) -> XAccount:
    """The account a call names ("@handle" or its numeric ID). Without one: the only account, or, with
    several, the first one added for a read that isn't about "you"; a write (``required``) is refused."""
    try:
        migrate()
    except XAccountError as exc:
        logger.info("The X sign-in from before 5.3 isn't an account yet (%s)", exc)
    accounts = listed()
    if not accounts:
        if legacy_pending():
            raise XAccountChoice("Row-Bot couldn't reach X to finish setting up the X account. Try again shortly.")
        raise XAccountChoice("No X account is connected. The person can connect one in Settings › Apps › X.")
    wanted = str(account or "").strip()
    if wanted:
        key = wanted.lstrip("@").casefold()
        found = next((item for item in accounts if item.id == key or item.username.casefold() == key), None)
        if found is None:
            raise XAccountChoice(f"No connected X account is {wanted}. Connected: {names(accounts)}.")
        return found
    if len(accounts) == 1 or not required:
        return accounts[0]
    raise XAccountChoice(f"Which X account? {names(accounts)}. Nothing was sent. Ask the person which one, "
                         "then call again with account set to it.")
