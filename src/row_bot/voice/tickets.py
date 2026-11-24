"""Single-use tickets that bind a voice WebSocket to the lease an authenticated start took.

Row-Bot Voice and Live voice both open a socket on Row-Bot's own origin; the
first message must present a ticket from that session's start (or a renewal),
which names the client session, conversation and lease, once.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hmac
import secrets
import threading

from row_bot.voice.client_transport import DictationError, DictationHandle, DictationOwner

TICKET_SECONDS = 30.0
MAX_TICKETS = 8


@dataclass(frozen=True)
class VoiceTicket:
    ticket: str
    expires_in_ms: int


@dataclass(frozen=True)
class RedeemedTicket:
    owner: DictationOwner
    handle: DictationHandle
    # The client session's CSRF token, so the socket can keep checking that session.
    csrf: str


@dataclass
class _Ticket:
    owner: DictationOwner
    handle: DictationHandle
    csrf: str
    expires: float


class VoiceTickets:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._tickets: dict[str, _Ticket] = {}
        self._lock = threading.Lock()

    def issue(self, owner: DictationOwner, handle: DictationHandle, csrf: str) -> VoiceTicket:
        value = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            for key, item in list(self._tickets.items()):
                if item.expires <= now or item.handle != handle and item.owner.client_session_id == owner.client_session_id:
                    del self._tickets[key]
            if len(self._tickets) >= MAX_TICKETS:
                raise DictationError("voice_session_busy")
            self._tickets[value] = _Ticket(owner, handle, csrf, now + TICKET_SECONDS)
        return VoiceTicket(value, int(TICKET_SECONDS * 1000))

    def redeem(self, ticket: object) -> RedeemedTicket:
        """The ticket's session, once: a second use, an expired or unknown ticket is refused."""
        if not isinstance(ticket, str) or not 20 <= len(ticket) <= 128:
            raise DictationError("action_denied")
        with self._lock:
            match = next((key for key in self._tickets if hmac.compare_digest(key, ticket)), None)
            item = self._tickets.pop(match) if match is not None else None
        if item is None or item.expires <= self._clock():
            raise DictationError("action_denied")
        return RedeemedTicket(item.owner, item.handle, item.csrf)

    def drop(self, handle: DictationHandle) -> None:
        with self._lock:
            for key, item in list(self._tickets.items()):
                if item.handle == handle:
                    del self._tickets[key]
