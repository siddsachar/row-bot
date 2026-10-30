"""First sign-in with a setup code (H1).

A server-mode Row-Bot nobody owns yet is claimed in a browser, with no terminal.
At each start ``row-bot serve`` settles the one code that may sign in: the host's
``ROW_BOT_SETUP_CODE`` (taken out of the environment, so no program Row-Bot
starts inherits it), or else a new code of its own, so the last one stops
working. Only its salted verifier is kept, and it signs in once. While nobody
owns this Row-Bot the code is printed to the console, which is the host's log.

A *new* host code also works once after someone owns this Row-Bot: it signs out
every other browser (recovery). It is never printed.

Wrong tries are counted per client address, as the access layer resolves it
(forwarded addresses only from trusted proxies). Five wrong tries pause that
address for 15 minutes. The pause is not what keeps a code safe: a generated
code has 60 random bits and a host code at least 12 letters or digits, so
guessing fails at any rate. A pause shared by everyone would only let a
stranger keep the owner out, so there is none.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
import math
import os
import secrets
import sys
import threading
import time
from typing import Any, Callable, Mapping, MutableMapping

from row_bot.access.service import AccessService, SetupCodeClaim
from row_bot.access.store import (
    SETUP_CODE_ACTIVE_KEY,
    SETUP_CODES_USED_KEY,
    AccessStore,
)
from row_bot.access.tokens import hash_secret, verify_secret
from row_bot.environment_secrets import take_secret

logger = logging.getLogger(__name__)

SETUP_CODE_ENV = "ROW_BOT_SETUP_CODE"
# Crockford's base32 has no I, L, O or U, so no symbol reads as another; 12 carry 60 bits.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 12
MAX_CODE_LENGTH = 64
FAILURE_LIMIT = 5
# From this many wrong tries on, the page says how many are left before the pause.
WARN_FROM = 3
PAUSE_SECONDS = 15 * 60
_MAX_TRACKED_CLIENTS = 1024
_DASHES = str.maketrans("", "", "-‐‑‒–—―−")
_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def normalize_setup_code(value: object) -> str:
    """The code a person typed or pasted: spaces, dashes and case don't matter, and O, I and L read as 0, 1, 1."""
    text = "".join(str(value or "").split()).translate(_DASHES).upper()
    return text.translate(_LOOKALIKES)


def format_setup_code(code: str) -> str:
    """``ABCD-EFGH-JKMN``: groups of four, easy to read out and type."""
    return "-".join(code[index : index + 4] for index in range(0, len(code), 4))


def generate_setup_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _well_formed(code: str) -> bool:
    return CODE_LENGTH <= len(code) <= MAX_CODE_LENGTH and code.isascii() and code.isalnum()


def _verifies(code: str, verifier: Any) -> bool:
    if not isinstance(verifier, Mapping):
        return False
    return verify_secret(
        code,
        salt=str(verifier.get("salt") or ""),
        expected_hash=str(verifier.get("hash") or ""),
    )


def _console() -> logging.Logger:
    """A logger whose only handler is this process's console stream, where hosts show logs.

    The code must reach a host's log view (PikaPods: More › Show Logs) and nothing that
    persists: it never propagates to Row-Bot's own log files.
    """
    console = logging.getLogger(f"{__name__}.console")
    console.propagate = False
    console.setLevel(logging.INFO)
    for handler in console.handlers[:]:
        console.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    console.addHandler(handler)
    return console


def prepare_setup_code_at_start(
    environ: MutableMapping[str, str] | None = None,
    *,
    store: AccessStore | None = None,
) -> None:
    """Settle the setup code for this start of ``row-bot serve``, before it starts any program.

    The host's code is taken out of the environment. It counts if it never signed anyone in;
    otherwise, while nobody owns this Row-Bot, a new code replaces the last one. While nobody owns
    it, the code is printed to the console. Once someone does, only a new host code is kept, as
    a recovery code, and it is not printed.
    """
    env = os.environ if environ is None else environ
    raw = take_secret(SETUP_CODE_ENV, env)
    selected = store or AccessStore()
    used = selected.get_meta(SETUP_CODES_USED_KEY) or []
    code: str | None = None
    if raw is not None:
        code = normalize_setup_code(raw)
        if not _well_formed(code):
            # Never the value: only the console ever shows a code.
            logger.warning(
                "%s needs at least %d letters or digits; Row-Bot ignores the one set",
                SETUP_CODE_ENV,
                CODE_LENGTH,
            )
            code = None
        elif any(_verifies(code, item) for item in used):
            code = None
    source = "host"
    owned = bool(selected.list_devices(include_revoked=False))
    if code is None and not owned:
        code, source = generate_setup_code(), "generated"
    if code is None:
        selected.set_meta(SETUP_CODE_ACTIVE_KEY, None)
        return
    verifier = hash_secret(code)
    selected.set_meta(
        SETUP_CODE_ACTIVE_KEY,
        {"salt": verifier.salt, "hash": verifier.secret_hash, "source": source},
    )
    if not owned:
        rule = "=" * 72
        _console().info(
            "%s\nRow-Bot setup code: %s (open your Row-Bot and enter it)\n%s",
            rule,
            format_setup_code(code),
            rule,
        )


class SetupOutcome(StrEnum):
    SIGNED_IN = "signed_in"
    WRONG = "wrong_code"
    USED = "code_used"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class SetupAttempt:
    outcome: SetupOutcome
    # Only once a few tries went wrong: how many are left before the pause.
    tries_left: int | None = None
    # Seconds until this client may try again.
    retry_after: int | None = None
    claim: SetupCodeClaim | None = None


@dataclass(slots=True)
class _Tries:
    count: int
    last: float
    paused_until: float = 0.0


def _client_key(client: str | None) -> str:
    return str(client or "unidentified")[:128]


class SetupCodes:
    """Setup-code sign-in for one server process."""

    def __init__(
        self,
        access: AccessService,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.access = access
        self._clock = clock
        self._lock = threading.Lock()
        self._tries: dict[str, _Tries] = {}

    def pause_remaining(self, client: str | None) -> int:
        """Seconds before this client may try a code again (0 when it may now)."""
        with self._lock:
            return self._paused_for(_client_key(client), self._clock())

    def sign_in(
        self,
        code: str,
        *,
        client: str | None,
        user_agent: str | None = None,
    ) -> SetupAttempt:
        key = _client_key(client)
        with self._lock:
            clock = self._clock()
            remaining = self._paused_for(key, clock)
            if remaining:
                return SetupAttempt(SetupOutcome.PAUSED, retry_after=remaining)
            candidate = normalize_setup_code(code)
            if not _well_formed(candidate):
                return self._wrong(key, clock)
            store = self.access.store
            active = store.get_meta(SETUP_CODE_ACTIVE_KEY)
            if not _verifies(candidate, active):
                used = store.get_meta(SETUP_CODES_USED_KEY) or []
                # The newest retired code is the one people bring again ("a code after claim").
                if used and _verifies(candidate, used[0]):
                    return SetupAttempt(SetupOutcome.USED)
                return self._wrong(key, clock)
            claim = self.access.claim_setup_code(
                used_verifier=active,
                allow_recovery=active.get("source") == "host",
                user_agent=user_agent,
                effective_client=client,
            )
            if claim is None:
                # Someone owns this Row-Bot now, and only a host's code may recover it.
                return SetupAttempt(SetupOutcome.USED)
            self._tries.pop(key, None)
            return SetupAttempt(SetupOutcome.SIGNED_IN, claim=claim)

    def _paused_for(self, key: str, clock: float) -> int:
        tries = self._tries.get(key)
        if tries is None or tries.paused_until <= clock:
            return 0
        return math.ceil(tries.paused_until - clock)

    def _wrong(self, key: str, clock: float) -> SetupAttempt:
        tries = self._tries.get(key)
        if tries is None or clock - tries.last >= PAUSE_SECONDS:
            tries = _Tries(count=0, last=clock)
        tries.count += 1
        tries.last = clock
        if tries.count >= FAILURE_LIMIT:
            tries.count = 0
            tries.paused_until = clock + PAUSE_SECONDS
        self._remember(key, tries, clock)
        if tries.paused_until > clock:
            return SetupAttempt(SetupOutcome.PAUSED, retry_after=PAUSE_SECONDS)
        left = FAILURE_LIMIT - tries.count
        return SetupAttempt(
            SetupOutcome.WRONG,
            tries_left=left if tries.count >= WARN_FROM else None,
        )

    def _remember(self, key: str, tries: _Tries, clock: float) -> None:
        self._tries[key] = tries
        if len(self._tries) <= _MAX_TRACKED_CLIENTS:
            return
        self._tries = {
            name: value
            for name, value in self._tries.items()
            if value.paused_until > clock or clock - value.last < PAUSE_SECONDS
        }
        while len(self._tries) > _MAX_TRACKED_CLIENTS:
            self._tries.pop(next(iter(self._tries)))


__all__ = [
    "CODE_ALPHABET",
    "FAILURE_LIMIT",
    "PAUSE_SECONDS",
    "SETUP_CODE_ENV",
    "SetupAttempt",
    "SetupCodes",
    "SetupOutcome",
    "format_setup_code",
    "generate_setup_code",
    "normalize_setup_code",
    "prepare_setup_code_at_start",
]
