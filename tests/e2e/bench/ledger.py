"""The benchmark cost ledger: every model call counted, paid calls stopped at a hard cap.

Three kinds of call, by provider:

- **plan**: the ChatGPT subscription (``codex``) through the eval profile. Costs $0 and has
  no cap (the owner's choice, 14 Nov 2026); its tokens are counted per day for the report.
- **local**: Ollama and other local servers. Costs $0; still counted.
- **api**: every other provider. Priced from the provider's own figure or the rates file
  (``ROW_BOT_LIVE_EVAL_RATES``, never guessed) and held by the paid cap
  (``ROW_BOT_BENCH_MAX_USD``, default **$0**: no paid call is sent at all). A paid call
  with no rate is refused, because it could not be held.

Every call is checked *before* it is sent, against an upper-bound estimate, and
recorded after it ends. The usage book (``ROW_BOT_BENCH_USAGE_BOOK``, default
``.tmp/bench/usage-book.json``) carries the project's paid total and the plan's
tokens per day across runs, so the project cap (``ROW_BOT_BENCH_PROJECT_USD``,
default $100) holds over every run, not just one.

The eval profile is still booked before each use (``booking``): that protects another
lane's sign-in, not a quota.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

from tests.e2e.live_eval.pricing import CallTokens, Rates, price_call, rates_for, upper_bound_usd

REPO_ROOT = Path(__file__).resolve().parents[3]
MAX_USD_ENV = "ROW_BOT_BENCH_MAX_USD"
PROJECT_USD_ENV = "ROW_BOT_BENCH_PROJECT_USD"
USAGE_BOOK_ENV = "ROW_BOT_BENCH_USAGE_BOOK"
DEFAULT_MAX_USD = 0.0
DEFAULT_PROJECT_USD = 100.0

PLAN = "plan"
LOCAL = "local"
API = "api"
PLAN_PROVIDERS = frozenset({"codex"})
LOCAL_PROVIDERS = frozenset({"ollama", "llama_cpp", "lmstudio", "vllm"})


class BudgetStop(RuntimeError):
    """Raised before a paid call that would pass the paid cap."""


def billing_for(provider: str) -> str:
    name = str(provider or "").strip().lower()
    if name in PLAN_PROVIDERS:
        return PLAN
    if name in LOCAL_PROVIDERS or name.startswith("custom_local"):
        return LOCAL
    return API


def _number(raw: str | None, default: float, name: str, *, allow_zero: bool) -> float:
    text = str(raw or "").strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a number") from exc
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{name} must be {'zero or more' if allow_zero else 'positive'}")
    return value


@dataclass(frozen=True)
class LedgerCaps:
    """The run's paid cap (USD) and the project's paid cap (USD). Plan and local calls have no cap."""

    max_usd: float = DEFAULT_MAX_USD
    project_usd: float = DEFAULT_PROJECT_USD

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LedgerCaps:
        source = os.environ if env is None else env
        return cls(
            max_usd=_number(source.get(MAX_USD_ENV), DEFAULT_MAX_USD, MAX_USD_ENV, allow_zero=True),
            project_usd=_number(source.get(PROJECT_USD_ENV), DEFAULT_PROJECT_USD, PROJECT_USD_ENV, allow_zero=True),
        )

    def describe(self) -> str:
        paid = "no paid calls" if self.max_usd <= 0 else f"${self.max_usd:.2f} paid this run"
        return f"{paid} (project cap ${self.project_usd:.2f}); plan and local calls counted, not capped"


def usage_book_path(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    text = str(source.get(USAGE_BOOK_ENV, "") or "").strip()
    return Path(text) if text else REPO_ROOT / ".tmp" / "bench" / "usage-book.json"


class UsageBook:
    """What the benchmark project has used across runs: paid USD in total, plan tokens per day.

    One small JSON file, rewritten atomically after every call. Runs are sequential (one
    worker at a time), so there is no cross-process lock.
    """

    def __init__(self, path: Path, *, today: Callable[[], str] | None = None) -> None:
        self.path = Path(path)
        self._today = today or (lambda: date.today().isoformat())

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("paid_usd_total", 0.0)
        data.setdefault("plan_tokens_by_day", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def paid_total(self) -> float:
        return float(self._read()["paid_usd_total"])

    def plan_today(self) -> int:
        return int(self._read()["plan_tokens_by_day"].get(self._today(), 0))

    def add(self, *, paid_usd: float = 0.0, plan_tokens: int = 0) -> None:
        if not paid_usd and not plan_tokens:
            return
        data = self._read()
        data["paid_usd_total"] = float(data["paid_usd_total"]) + max(0.0, float(paid_usd))
        days = data["plan_tokens_by_day"]
        days[self._today()] = int(days.get(self._today(), 0)) + max(0, int(plan_tokens))
        self._write(data)


@dataclass
class CallEntry:
    """One model call as the ledger keeps it: counts, cost, latency and settings, never text."""

    provider: str
    model: str
    purpose: str = "other"
    task_id: str = ""
    phase: str = ""
    billing: str = ""
    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None
    cost_usd: float | None = None
    cost_source: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.billing = self.billing or billing_for(self.provider)

    @property
    def tokens(self) -> int:
        return int(self.input_tokens or 0) + int(self.output_tokens or 0)


@dataclass
class Totals:
    calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    unpriced_calls: int = 0

    def add(self, entry: CallEntry) -> None:
        self.calls += 1
        self.input_tokens += int(entry.input_tokens or 0)
        self.cache_read_tokens += int(entry.cache_read_tokens or 0)
        self.output_tokens += int(entry.output_tokens or 0)
        self.reasoning_tokens += int(entry.reasoning_tokens or 0)
        self.latency_ms += int(entry.latency_ms or 0)
        if entry.cost_usd is None:
            self.unpriced_calls += 1
        else:
            self.cost_usd += entry.cost_usd


def totals(entries: list[CallEntry], key: Callable[[CallEntry], str] | None = None) -> dict[str, dict[str, Any]]:
    """Totals of *entries*, grouped by ``key`` (default: billing kind)."""
    groups: dict[str, Totals] = {}
    for entry in entries:
        groups.setdefault((key or (lambda item: item.billing))(entry), Totals()).add(entry)
    return {name: asdict(value) for name, value in sorted(groups.items())}


class CostLedger:
    """Checks each call before it is sent and records it after; writes ``ledger.jsonl``."""

    def __init__(self, caps: LedgerCaps, book: UsageBook, *, rates: Mapping[str, Rates] | None = None,
                 path: Path | None = None) -> None:
        self.caps = caps
        self.book = book
        self.rates = dict(rates or {})
        self.path = path
        self.entries: list[CallEntry] = []
        self.refused: list[str] = []
        self.run_paid_usd = 0.0

    def paid_room(self) -> float:
        """USD a paid call may still use: the run's cap and the project's, whichever is lower."""
        return max(0.0, min(self.caps.max_usd - self.run_paid_usd, self.caps.project_usd - self.book.paid_total()))

    def check(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> None:
        """Raise :class:`BudgetStop` when this call, estimated high, would pass a cap."""
        if billing_for(provider) in (LOCAL, PLAN):
            return  # free; counted when recorded
        room = self.paid_room()
        if room <= 0:
            self._refuse(f"paid cap: ${room:.4f} left (run cap ${self.caps.max_usd:.2f}, "
                         f"project cap ${self.caps.project_usd:.2f}); {provider}:{model} not sent")
        usd = upper_bound_usd(input_tokens, output_tokens, rates_for(self.rates, provider, model))
        if usd is None:
            self._refuse(f"no rate for {provider}:{model} in the rates file; a paid call is never guessed")
        if usd > room:
            self._refuse(f"paid cap: ${usd:.4f} estimated > ${room:.4f} left")

    def _refuse(self, reason: str) -> None:
        self.refused.append(reason)
        raise BudgetStop(f"benchmark budget: {reason}")

    def record(self, entry: CallEntry) -> CallEntry:
        entry.billing = entry.billing or billing_for(entry.provider)
        if entry.billing in (PLAN, LOCAL):
            entry.cost_usd, entry.cost_source = 0.0, entry.billing
        else:
            price = price_call(CallTokens(input=entry.input_tokens, cache_read=entry.cache_read_tokens,
                                          output=entry.output_tokens, cost=entry.cost_usd),
                               rates_for(self.rates, entry.provider, entry.model))
            entry.cost_usd, entry.cost_source = price.usd, price.source
        self.entries.append(entry)
        paid = entry.cost_usd if entry.billing == API and entry.cost_usd else 0.0
        self.run_paid_usd += paid
        self.book.add(paid_usd=paid, plan_tokens=entry.tokens if entry.billing == PLAN else 0)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(entry), sort_keys=True) + "\n")
        return entry
