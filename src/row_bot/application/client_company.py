"""Settings › Company (Row-Bot for Teams, C1b): join this Row-Bot to a company's server, see who and which
company, sign in again, leave.

- Reading the state contacts nothing. With no company joined it looks for the record file only and imports
  nothing that opens a socket; nothing runs at start-up.
- Join and Sign in again open this computer's browser, which comes back to a loopback address here, so only
  the desktop app on this computer starts them (the routes check). Join first asks the address's ``/healthz``,
  then waits in the background for the browser; the state reads ``waiting`` until it is done.
- Check is the one light call Settings makes when it opens on a joined Row-Bot (and its Retry): the company's
  policy, then ``/api/me`` and its models, refreshing the credential if it is about to end.
- Leave tells the server, then forgets the credential and the record; if the server can't be reached it still
  forgets them here and says so.
- A company server set by this computer's IT department (C5, ``company.managed``): Join goes only there, and
  Leave and Sign in again are refused (409 ``company_pinned``) except to leave a server IT moved it away from.
"""

from __future__ import annotations

import logging
import platform
import secrets
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from row_bot.company import managed, store

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()


@dataclass
class _Join:
    origin: str
    device_name: str
    # Set when signing in again as the same device.
    device_id: str | None
    verifier: str
    loopback: Any
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


_join: _Join | None = None
# How the last Join or Sign in again ended, when it didn't join (a word the client puts into words).
_failure: str | None = None
# After Leave: "left", or "left_offline" when the server couldn't be told; and the company it left.
_notice: str | None = None
_left: str | None = None
# The last check couldn't use the server: "offline", "busy" (it asked to wait) or "storage_unavailable" (the
# keychain was locked). The Row-Bot stays joined.
_unreachable: str | None = None
# A Sign in again with the device id didn't finish (the server may refuse that id): the next one joins afresh.
_fresh_next = False


def computer_name() -> str:
    """This computer's name, as the company's server shows it to its admins."""
    name = "".join(ch for ch in platform.node() if ch.isprintable())[:200].strip()
    return name or "This computer"


def _device_name(given: str) -> str:
    name = "".join(ch for ch in given if ch.isprintable())[:200].strip()
    return name or computer_name()


def _company(origin: str) -> str:
    return urlsplit(origin).hostname or origin


def _origin(address: str) -> str | None:
    """The origin of a typed address, or None when it isn't one."""
    from row_bot.company import client

    try:
        return client.normalise_origin(address)
    except client.InvalidAddress:
        return None


def status() -> dict[str, Any]:
    """The state, from the record and this process only."""
    with _LOCK:
        record = store.read_record()
        join = _join
        if join is not None:
            state = "waiting"
        elif record is None:
            state = "not_joined"
        elif record.disconnected:
            state = "disconnected"
        else:
            state = "unreachable" if _unreachable else "joined"
        failure = None if join else (_unreachable if state == "unreachable" and _unreachable != "offline"
                                     else _failure)
        origin = record.origin if record else (join.origin if join else None)
        from row_bot.company import enforce, policy, provider

        suggested = provider.suggestion(record)
        rules = policy.state()
        pin = managed.pin()
        return {
            "schema_version": 1,
            "state": state,
            "company": _company(origin) if origin else _left,
            "origin": origin,
            "name": (record.name or None) if record else None,
            "email": (record.email or None) if record else None,
            "joined_at": record.joined_at if record else None,
            "computer_name": record.device_name if record else computer_name(),
            "rejoining": bool(join and join.device_id),
            "failure": failure,
            "notice": None if join or record else _notice,
            # Company models (C2): how many the company offers (None until its catalog is read) and the one
            # it suggests to a person who hasn't chosen a model.
            "model_count": provider.model_count(record),
            "suggested_model_ref": suggested.ref if suggested else None,
            "suggested_model_name": suggested.name if suggested else None,
            # The company's policy (C3a): whether it is in force, cached or locked down, why and since when; the
            # settings it restricts ("Set by your company"); and every setting in plain words.
            "policy_state": rules.state,
            "policy_reason": rules.reason,
            "policy_since": policy.iso(rules.since),
            "locked": policy.locked(rules.policy),
            "rules": policy.rules(rules.policy),
            # What the client shows as "Set by your company" beyond models (C3b): tools switched off, the
            # approval ceiling, chat export and which apps may be added; None with no company.
            "limits": enforce.limits(rules.policy),
            # The company server this computer's IT department set (C5): Join goes only there and Leave isn't
            # offered, unless the Row-Bot is joined somewhere else (IT moved it). And whether IT set one that
            # isn't a valid address (ignored).
            "pinned_server": pin.server,
            "pin_invalid": pin.invalid,
        }


def _failed(reason: str) -> dict[str, Any]:
    global _failure
    with _LOCK:
        _failure = reason
    logger.info("Joining a company didn't start (%s)", reason)
    return status()


def join(address: str, device_name: str = "") -> dict[str, Any]:
    """Join the company at ``address``: check it, then open the browser at its sign-in."""
    record = store.read_record()
    pinned = managed.pinned_server()
    if pinned is not None:
        if _origin(address) != pinned:
            raise managed.CompanyPinned()
        if record is not None and record.origin != pinned:
            # IT moved this Row-Bot: it leaves the company it was in, then signs in to the one IT set.
            device_name = device_name or record.device_name
            leave()
            record = None
    if record is not None and not record.disconnected:
        return status()  # Joined already: Leave first.
    return _start(address, device_name, device_id=None)


def sign_in_again() -> dict[str, Any]:
    """Sign in again as the same device (a disconnected Row-Bot, or the monthly sign-in)."""
    record = store.read_record()
    if record is None:
        return status()
    pinned = managed.pinned_server()
    if pinned is not None and record.origin != pinned:
        raise managed.CompanyPinned()  # IT moved it: sign in to the server IT set instead.
    with _LOCK:
        fresh = _fresh_next
    return _start(record.origin, record.device_name, device_id=None if fresh else record.device_id)


def _start(address: str, device_name: str, *, device_id: str | None) -> dict[str, Any]:
    global _join, _failure, _notice, _left
    with _LOCK:
        if _join is not None:
            return status()
        _failure = _notice = _left = None
    from row_bot.company import client, sign_in

    try:
        origin = client.normalise_origin(address)
    except client.InvalidAddress as invalid:
        return _failed(invalid.reason)
    if not store.storage_available():
        return _failed("storage_unavailable")  # Before anything is asked of the server.
    try:
        client.check_server(origin)
    except client.NotCompanyServer:
        return _failed("not_company_server")
    except client.Busy:
        return _failed("busy")
    except client.Unreachable:
        return _failed("unreachable")
    verifier, challenge = sign_in.pkce()
    state = secrets.token_urlsafe(32)
    name = _device_name(device_name)
    loopback = sign_in.Loopback(state)
    started = _Join(origin=origin, device_name=name, device_id=device_id, verifier=verifier, loopback=loopback)
    with _LOCK:
        if _join is not None:  # Another Join started meanwhile.
            loopback.close()
            return status()
        _join = started
    try:
        sign_in.open_browser(sign_in.authorize_url(origin, redirect_uri=loopback.redirect_uri, challenge=challenge,
                                                   state=state, device_name=name, device_id=device_id))
    except Exception:
        loopback.close()
        with _LOCK:
            _join = None
        return _failed("browser")
    started.thread = threading.Thread(target=_finish, args=(started,), daemon=True, name="row-bot-company-join")
    started.thread.start()
    return status()


def _keep(join: _Join, grant: Any) -> None:
    """Save the new credential (keychain first), ask who signed in, then write the record."""
    from row_bot.company import client

    store.save_credential(grant.credential)
    name = email = ""
    try:
        name, email = client.me(join.origin)
    except client.Unreachable:
        pass  # Joined; the next check fills them in.
    before = store.read_record()
    joined_at = (before.joined_at if before and before.origin == join.origin and before.device_id == grant.device_id
                 else datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        store.write_record(store.Record(origin=join.origin, device_id=grant.device_id, company=_company(join.origin),
                                        name=name, email=email, device_name=join.device_name, joined_at=joined_at))
    except OSError:
        store.delete_credential()
        raise


def _finish(join: _Join) -> None:
    """Wait for the browser, then exchange the code and keep what it gives (the join's thread)."""
    global _join, _failure, _unreachable, _fresh_next
    from row_bot.company import client, sign_in

    failure: str | None = None
    try:
        try:
            answer = join.loopback.wait(join.stop)
        finally:
            join.loopback.close()
        if not answer["code"]:
            failure = "denied"  # Cancel on the company's page.
        else:
            _keep(join, client.exchange_code(join.origin, code=answer["code"], verifier=join.verifier,
                                             redirect_uri=join.loopback.redirect_uri))
    except sign_in.Cancelled:
        failure = "cancelled"
    except sign_in.TimedOut:
        failure = "timed_out"
    except client.Busy:
        failure = "busy"
    except (client.Refused, client.Unreachable, client.Disconnected):
        failure = "failed"
    except store.StorageUnavailable:
        failure = "storage_unavailable"
    except Exception as error:
        logger.warning("Joining a company failed unexpectedly (%s)", type(error).__name__)
        failure = "failed"
    with _LOCK:
        _join = None
        _failure = failure
        if failure is None:
            _unreachable = None
            _fresh_next = False
        elif join.device_id and failure != "denied":
            _fresh_next = True
    logger.info("Joining a company %s", f"didn't finish ({failure})" if failure else "finished")
    if failure is None:
        from row_bot.company import policy, provider

        policy.refresh()  # Its rules first, so the lists below obey them.
        provider.after_join()  # Its models, in every model list.


def cancel() -> dict[str, Any]:
    """Stop waiting for the browser."""
    with _LOCK:
        join = _join
    if join is not None:
        join.stop.set()
        if join.thread is not None:
            join.thread.join(timeout=5)
    return status()


def check() -> dict[str, Any]:
    """One light call: whether the company still accepts this Row-Bot, and who is signed in."""
    global _unreachable
    record = store.read_record()
    with _LOCK:
        busy = _join is not None
    if record is None or record.disconnected or busy:
        return status()
    from row_bot.company import client, policy, provider

    policy.refresh()  # Settings opened (or its Retry): the company's rules, read again. It keeps its failures.
    record = store.read_record()
    if record is None or record.disconnected:
        return status()
    reach: str | None = None
    try:
        name, email = client.me(record.origin)
        current = store.read_record()
        if (current and current.device_id == record.device_id and not current.disconnected
                and (current.name, current.email) != (name, email)):
            store.write_record(replace(current, name=name, email=email))
        try:
            provider.refresh()  # Settings opened: the company's models, read again.
        except client.Unreachable as error:
            logger.info("The company's models couldn't be read (%s); the lists keep what they had",
                        type(error).__name__)
    except client.Disconnected:
        pass
    except client.Busy:
        reach = "busy"
    except client.Unreachable:
        reach = "offline"
    except store.StorageUnavailable:
        reach = "storage_unavailable"
    with _LOCK:
        _unreachable = reach
    return status()


def leave() -> dict[str, Any]:
    """Leave the company: tell its server, then forget the credential and the record here."""
    global _notice, _left, _failure, _unreachable, _fresh_next
    with _LOCK:
        if _join is not None:
            return status()
    record = store.read_record()
    if record is None:
        return status()
    if record.origin == managed.pinned_server():
        raise managed.CompanyPinned()  # IT set this company for this computer.
    from row_bot.company import audit

    audit.leave()  # One last send of what's waiting for the company, then the audit outbox goes (C4).
    told = True
    if not record.disconnected:
        from row_bot.company import client

        try:
            client.leave(record.origin)
        except client.Disconnected:
            pass  # The server had already let it go.
        except (client.Unreachable, store.StorageUnavailable):
            told = False
    store.delete_credential()
    store.delete_record()
    from row_bot.company import policy, provider

    policy.forget()  # Its rules go with it,
    provider.forget()  # and its models leave every model list.
    with _LOCK:
        _notice, _left = ("left" if told else "left_offline"), record.company
        _failure = _unreachable = None
        _fresh_next = False
    logger.info("This Row-Bot left its company%s", "" if told else " (the server couldn't be told)")
    return status()


def wait(timeout: float = 10.0) -> None:
    """Wait for a Join in progress to end (a test's way to see its outcome)."""
    with _LOCK:
        join = _join
    if join is not None and join.thread is not None:
        join.thread.join(timeout)
