"""Learning's spending (D25, E13): the monthly token limit.

Learning's own model calls (testing a change, judging, diagnosis, teacher and strategy
trials) are tagged ``learning_*`` in the usage ledger (``usage.db``) and run on the
default chat model at the lowest effort (``providers.background``, the cost plan's D1
rule). What they used this calendar month, in local time, counts against the person's
``monthly_token_limit`` (2M by default; 0 means no Learning model calls, O5; ``None`` means no limit, the
person's choice: nothing is refused, and what is used still counts and shows).

- **Tokens** are what the ledger counts per call: input (cache reads and writes
  included) plus output.
- **Local models don't count** (D25): Ollama, and custom endpoints that run on this
  computer.
- A :class:`TokenMeter` is one run's view of the allowance: what the ledger says was
  used this month when the run starts, plus what the run spends. It refuses a call that
  would pass the limit *before* it is sent, so a run stops cleanly and says so.
- **Comparing methods keeps its share** (O178): the strategy trials may use at most
  :data:`TRIAL_SHARE` of a fixed limit, and what of it they haven't used this month is kept
  back from Learning's other work, so the rest of the month can't use it up first.

Reading the ledger never creates it (it is opened read-only).
"""

from __future__ import annotations

import ipaddress
import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

LEARNING_PURPOSES: tuple[str, ...] = ("learning_diagnosis", "learning_test", "learning_teacher", "learning_meta")
#: The strategy trials' calls (comparing methods, ``improver.meta``) and their share of a fixed monthly limit (E13).
TRIAL_PURPOSE = "learning_meta"
TRIAL_SHARE = 0.20
LOCAL_PROVIDERS = frozenset({"ollama"})
#: What no limit leaves a run: more tokens than a month of Learning's work uses, so the meter keeps whole numbers.
UNLIMITED_TOKENS = 10**12
# ``allowance``'s default: the person's saved limit (``None`` is a limit of its own, no limit).
_SAVED: Any = object()


def month_start(now: float | None = None) -> float:
    """The start of the current calendar month, local time, as epoch seconds."""

    moment = datetime.fromtimestamp(time.time() if now is None else float(now))
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def is_local_provider(provider_id: str) -> bool:
    """Whether a provider's calls run on this computer (they cost no tokens of the limit)."""

    provider = str(provider_id or "").strip()
    if provider in LOCAL_PROVIDERS:
        return True
    if not provider.startswith("custom_openai_"):
        return False
    try:
        from row_bot.providers.custom import get_custom_endpoint

        endpoint = get_custom_endpoint(provider) or {}
        host = urlparse(str(endpoint.get("base_url") or "")).hostname or ""
    except Exception:
        logger.debug("Learning could not tell where %s runs; it counts", provider, exc_info=True)
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _ledger_path() -> Path:
    from row_bot.data_paths import get_usage_db_path

    return Path(get_usage_db_path(create_parent=False))


def used_this_month(
    *,
    now: float | None = None,
    path: Path | None = None,
    local: Callable[[str], bool] = is_local_provider,
    purposes: tuple[str, ...] = LEARNING_PURPOSES,
) -> int:
    """Tokens Learning's own calls used this month, local models left out (``purposes``: only those, e.g. the
    strategy trials' ``learning_meta``)."""

    ledger = path if path is not None else _ledger_path()
    if not ledger.is_file() or not purposes:
        return 0
    marks = ", ".join("?" for _ in purposes)
    try:
        with closing(sqlite3.connect(f"{ledger.resolve().as_uri()}?mode=ro", uri=True, timeout=5)) as connection:
            rows = connection.execute(
                "SELECT provider, COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0)"
                f" FROM model_usage WHERE created_at >= ? AND purpose IN ({marks}) GROUP BY provider",
                (month_start(now), *purposes),
            ).fetchall()
    except sqlite3.Error:
        logger.debug("Learning could not read the usage ledger", exc_info=True)
        return 0
    return sum(int(tokens or 0) for provider, tokens in rows if not local(str(provider or "")))


def kept_for_trials(limit: int | None, *, now: float | None = None) -> int:
    """Tokens of a fixed limit kept for comparing methods now: the trials' share less what they used this month."""

    if limit is None or limit <= 0:
        return 0
    return max(0, int(limit * TRIAL_SHARE) - used_this_month(now=now, purposes=(TRIAL_PURPOSE,)))


@dataclass(frozen=True)
class Allowance:
    """This month's Learning tokens: the limit (``None``: no limit), what is used, and what is kept back for
    comparing methods (0 for the trials themselves)."""

    limit: int | None
    used: int
    month_start: float
    kept_back: int = 0

    @property
    def remaining(self) -> int:
        if self.limit is None:
            return UNLIMITED_TOKENS
        return max(0, self.limit - self.used - self.kept_back)


def allowance(*, now: float | None = None, limit: int | None = _SAVED, for_trials: bool = False) -> Allowance:
    """The monthly limit (the person's saved one unless ``limit`` is given) and what this month used. Only the
    strategy trials (``for_trials``) may use the share kept back for them."""

    if limit is _SAVED:
        from row_bot import learning

        limit = learning.read_settings().monthly_token_limit
    limit = None if limit is None else max(0, int(limit))
    kept = 0 if for_trials else kept_for_trials(limit, now=now)
    return Allowance(limit, used_this_month(now=now), month_start(now), kept)


def limit_note(limit: int | None, kept_back: int = 0) -> str:
    """What a run says when the limit stopped it."""

    if limit is None:
        return "Stopped: this run used the tokens it was given."
    if limit <= 0:
        return "Stopped: Learning's monthly token limit is 0, so it makes no model calls."
    if kept_back > 0:
        return (f"Stopped: Learning reached this month's token limit ({limit:,} tokens), apart from the "
                f"{kept_back:,} kept for comparing methods.")
    return f"Stopped: Learning reached this month's token limit ({limit:,} tokens)."


class TokenMeter:
    """One run's spending against the monthly limit (thread-compatible, not thread-safe).

    ``share`` limits the run to part of what is left (the recursive layer's trials get
    at most 20% of the month, E13); ``kept_back`` is what it may not use (the trials' share, O178). Local
    calls are recorded but never refused.
    """

    def __init__(self, limit: int | None, used: int = 0, *, share: float = 1.0, kept_back: int = 0) -> None:
        self.limit = None if limit is None else max(0, int(limit))
        self.used_before = max(0, int(used))
        self.kept_back = 0 if self.limit is None else max(0, int(kept_back))
        share = min(1.0, max(0.0, float(share)))
        left = UNLIMITED_TOKENS if self.limit is None else max(0, self.limit - self.used_before - self.kept_back)
        self.cap = int(left * share)
        self.spent = 0
        self.local_tokens = 0
        self.refused = 0

    @classmethod
    def for_month(cls, *, share: float = 1.0, now: float | None = None, limit: int | None = _SAVED) -> TokenMeter:
        current = allowance(now=now, limit=limit)
        return cls(current.limit, current.used, share=share, kept_back=current.kept_back)

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.refused > 0 or self.remaining <= 0

    def allows(self, estimated_tokens: int, *, local: bool = False) -> bool:
        """Whether a call of about ``estimated_tokens`` may be sent now. A refusal is remembered."""

        if local:
            return True
        if self.spent + max(0, int(estimated_tokens)) > self.cap:
            self.refused += 1
            return False
        return True

    def charge(self, tokens: int, *, local: bool = False) -> None:
        if local:
            self.local_tokens += max(0, int(tokens))
        else:
            self.spent += max(0, int(tokens))

    def note(self) -> str:
        return limit_note(self.limit, self.kept_back)


__all__ = [
    "Allowance",
    "LEARNING_PURPOSES",
    "LOCAL_PROVIDERS",
    "TRIAL_PURPOSE",
    "TRIAL_SHARE",
    "TokenMeter",
    "UNLIMITED_TOKENS",
    "allowance",
    "is_local_provider",
    "kept_for_trials",
    "limit_note",
    "month_start",
    "used_this_month",
]
