"""Records of a live eval run, the JSON and Markdown report, and the A/B diff.

A run writes ``report.json`` and ``report.md`` into its folder (by default
``.tmp/live-eval/<timestamp>/``, which git ignores). The JSON holds counts,
pass/fail and short answer excerpts only: never keys, never request bodies.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .pricing import CallTokens, Price, Rates, price_call, rates_for

SCHEMA_VERSION = 1
PASS, FAIL, ERROR, SKIPPED, BUDGET = "pass", "fail", "error", "skipped", "budget_stopped"
_ANSWER_EXCERPT = 240
# Key-shaped words a provider error might echo, even masked: sk-..., xai-..., AIza..., "Bearer ...".
_KEY_SHAPED = re.compile(
    r"\b(?:(?:sk|pk|rk|gsk|xai)[-_][A-Za-z0-9_\-*]{16,}|AIza[A-Za-z0-9_\-]{30,})|Bearer\s+[A-Za-z0-9._\-*]{16,}"
)


# Credentials this process holds only in memory (the ChatGPT lane's OAuth tokens): never written anywhere.
_IN_MEMORY_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    """Have :func:`redact` remove a credential that is not in the environment."""
    if value and len(value) >= 12:
        _IN_MEMORY_SECRETS.add(value)


def redact(text: str) -> str:
    """Remove credentials from text a record keeps: every key in the environment, credentials held in
    memory, and key-shaped words."""
    cleaned = str(text or "")
    for name, value in os.environ.items():
        if value and len(value) >= 12 and (name.endswith("_API_KEY") or name.endswith("_TOKEN")):
            cleaned = cleaned.replace(value, "[redacted]")
    for value in sorted(_IN_MEMORY_SECRETS, key=len, reverse=True):
        cleaned = cleaned.replace(value, "[redacted]")
    return _KEY_SHAPED.sub("[redacted]", cleaned)


@dataclass
class CallRecord:
    """One ledger row: one model call."""

    purpose: str = "other"
    provider: str = ""
    model: str = ""
    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cache_write_1h_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None
    cost: float | None = None
    created_at: float | None = None
    iteration: int | None = None
    thread: str = ""

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> CallRecord:
        known = {name for name in cls.__dataclass_fields__}
        values = {key: row[key] for key in row.keys() if key in known}
        values["thread"] = str(row.get("thread_id") or row.get("thread") or "")[:12]
        return cls(**values)

    def tokens(self) -> CallTokens:
        return CallTokens(
            input=self.input_tokens,
            cache_read=self.cache_read_tokens,
            cache_write=self.cache_write_tokens,
            cache_write_1h=self.cache_write_1h_tokens,
            output=self.output_tokens,
            reasoning=self.reasoning_tokens,
            cost=self.cost,
        )

    def cache_read_share(self) -> float | None:
        if not self.input_tokens or self.cache_read_tokens is None:
            return None
        return self.cache_read_tokens / self.input_tokens


@dataclass
class Totals:
    calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cache_write_5m_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    calls_without_counts: int = 0
    calls_with_cache_counts: int = 0
    cache_read_share: float | None = None
    latency_ms: int = 0
    ttft_ms_first: int | None = None
    ttft_ms_mean: float | None = None
    cost_usd: float | None = None
    cost_source: str = "unpriced"
    unpriced_calls: int = 0
    unpriced_reason: str = ""
    max_calls_per_minute: int = 0


def summarize_calls(calls: Sequence[CallRecord], rates: Mapping[str, Rates] | None = None) -> Totals:
    """Token, cache, latency and cost totals for a list of calls.

    The cache-read share counts only calls that reported cache counts, so a
    provider that reports none shows "unknown" rather than 0%.
    """
    totals = Totals(calls=len(calls))
    cache_basis = 0
    priced_usd = 0.0
    sources: set[str] = set()
    ttfts = [call.ttft_ms for call in calls if call.ttft_ms is not None]
    for call in calls:
        if call.input_tokens is None and call.output_tokens is None:
            totals.calls_without_counts += 1
        totals.input_tokens += int(call.input_tokens or 0)
        totals.cache_read_tokens += int(call.cache_read_tokens or 0)
        totals.cache_write_tokens += int(call.cache_write_tokens or 0)
        totals.cache_write_1h_tokens += int(call.cache_write_1h_tokens or 0)
        totals.output_tokens += int(call.output_tokens or 0)
        totals.reasoning_tokens += int(call.reasoning_tokens or 0)
        totals.latency_ms += int(call.latency_ms or 0)
        if call.cache_read_tokens is not None and call.input_tokens:
            totals.calls_with_cache_counts += 1
            cache_basis += int(call.input_tokens)
        price: Price = price_call(call.tokens(), rates_for(rates or {}, call.provider, call.model))
        if price.usd is None:
            totals.unpriced_calls += 1
            totals.unpriced_reason = totals.unpriced_reason or price.reason
        else:
            priced_usd += price.usd
            sources.add(price.source)
    totals.cache_write_5m_tokens = max(0, totals.cache_write_tokens - totals.cache_write_1h_tokens)
    cache_read_of_basis = sum(
        int(call.cache_read_tokens or 0) for call in calls if call.cache_read_tokens is not None and call.input_tokens
    )
    totals.cache_read_share = (cache_read_of_basis / cache_basis) if cache_basis else None
    if ttfts:
        totals.ttft_ms_first = ttfts[0]
        totals.ttft_ms_mean = sum(ttfts) / len(ttfts)
    priced_calls = totals.calls - totals.unpriced_calls
    if priced_calls:
        totals.cost_usd = priced_usd
        if totals.unpriced_calls:
            totals.cost_source = "partial"
        else:
            totals.cost_source = "provider" if sources == {"provider"} else "rates" if sources == {"rates"} else "mixed"
    totals.max_calls_per_minute = _max_calls_per_minute(calls)
    return totals


def _max_calls_per_minute(calls: Sequence[CallRecord]) -> int:
    """The most calls of one conversation that started within any 60 seconds (OpenAI's ~15/min per key)."""
    best = 0
    by_thread: dict[str, list[float]] = {}
    for call in calls:
        if call.created_at is not None:
            by_thread.setdefault(call.thread, []).append(float(call.created_at))
    for times in by_thread.values():
        times.sort()
        start = 0
        for end, moment in enumerate(times):
            while moment - times[start] > 60.0:
                start += 1
            best = max(best, end - start + 1)
    return best


@dataclass
class TaskRecord:
    task_id: str
    category: str
    provider: str
    model: str
    variant: str = "baseline"
    status: str = SKIPPED
    reason: str = ""
    duration_s: float = 0.0
    answer_excerpt: str = ""
    tool_calls: int = 0
    tool_results: int = 0
    tools_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    totals: Totals = field(default_factory=Totals)
    first_call_cache_read_share: float | None = None
    by_purpose: dict[str, int] = field(default_factory=dict)
    # Counts of known Row-Bot log lines during the task: "stable prefix" / "prefix broken"
    # (A1's DEBUG diff), A4a hygiene jumps, A2b drops and retries, cache retries, truncation.
    log_counts: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def finish(self, rates: Mapping[str, Rates] | None = None) -> TaskRecord:
        """Fill the totals from the calls."""
        self.totals = summarize_calls(self.calls, rates)
        main = [call for call in self.calls if call.purpose in {"main", "chat_only"}] or self.calls
        self.first_call_cache_read_share = main[0].cache_read_share() if main else None
        purposes: dict[str, int] = {}
        for call in self.calls:
            purposes[call.purpose] = purposes.get(call.purpose, 0) + 1
        self.by_purpose = purposes
        self.answer_excerpt = redact(self.answer_excerpt[:_ANSWER_EXCERPT])
        self.errors = [redact(error) for error in self.errors]
        self.reason = redact(self.reason)
        return self

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskRecord:
        values = dict(data)
        values["calls"] = [CallRecord(**call) for call in values.get("calls") or []]
        values["totals"] = Totals(**(values.get("totals") or {}))
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in values.items() if key in known})


@dataclass
class ProbeRecord:
    """A verification check's finding: accepted or not, plus what was observed."""

    check: str
    provider: str
    model: str = ""
    status: str = SKIPPED
    observed: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProbeRecord:
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass
class Report:
    run_id: str
    variant: str = "baseline"
    generated_at: str = ""
    caps: dict[str, Any] = field(default_factory=dict)
    spent: dict[str, Any] = field(default_factory=dict)
    providers: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[TaskRecord] = field(default_factory=list)
    probes: list[ProbeRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    schema: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["generated_at"] = self.generated_at or time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Report:
        values = dict(data)
        values["tasks"] = [TaskRecord.from_dict(task) for task in values.get("tasks") or []]
        values["probes"] = [ProbeRecord.from_dict(probe) for probe in values.get("probes") or []]
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in values.items() if key in known})

    @classmethod
    def load(cls, path: Path) -> Report:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# ── Markdown ───────────────────────────────────────────────────────────────


def _pct(value: float | None) -> str:
    return "unknown" if value is None else f"{value * 100:.0f}%"


def _usd(value: float | None, source: str = "") -> str:
    if value is None:
        return "unpriced"
    suffix = " (partial)" if source == "partial" else ""
    return f"${value:.4f}{suffix}"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def provider_summaries(tasks: Iterable[TaskRecord], rates: Mapping[str, Rates] | None = None) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[TaskRecord]] = {}
    for task in tasks:
        grouped.setdefault((task.provider, task.model), []).append(task)
    rows = []
    for (provider, model), group in sorted(grouped.items()):
        ran = [task for task in group if task.status in {PASS, FAIL, ERROR}]
        totals = summarize_calls([call for task in group for call in task.calls], rates)
        rows.append({
            "provider": provider,
            "model": model,
            "passed": sum(task.status == PASS for task in group),
            "ran": len(ran),
            "skipped": sum(task.status in {SKIPPED, BUDGET} for task in group),
            "totals": totals,
        })
    return rows


def render_markdown(report: Report, *, rates: Mapping[str, Rates] | None = None, manual: Sequence[Any] = ()) -> str:
    lines = [
        f"# Row-Bot live eval — {report.run_id}",
        "",
        f"- Variant: `{report.variant}`",
        f"- Generated: {report.generated_at or time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Caps: {report.caps.get('describe') or report.caps}",
        f"- Spent: {report.spent.get('tokens', 0):,} tokens; ${float(report.spent.get('usd') or 0):.4f} priced; "
        f"{report.spent.get('unpriced_tokens', 0):,} tokens unpriced",
        "",
    ]
    if report.notes:
        lines += ["## Notes", "", *[f"- {note}" for note in report.notes], ""]
    if report.providers:
        lines += ["## Providers", "", "| Provider | Model | Status | Reason |", "|---|---|---|---|"]
        for entry in report.providers:
            reason = str(entry.get("reason") or "")
            if entry.get("credentials"):
                reason = f"{reason} (sign-in tokens: {entry['credentials']})".strip()
            lines.append(
                f"| {_cell(entry.get('provider'))} | {_cell(entry.get('model', ''))} | "
                f"{_cell(entry.get('status', ''))} | {_cell(reason)} |"
            )
        lines.append("")
    summaries = provider_summaries(report.tasks, rates)
    if summaries:
        lines += [
            "## Summary per provider",
            "",
            "| Provider | Model | Passed | Input | Cache read | Output | Cache-read share | Cost |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for row in summaries:
            totals: Totals = row["totals"]
            skipped = f" ({row['skipped']} skipped)" if row["skipped"] else ""
            lines.append(
                f"| {_cell(row['provider'])} | {_cell(row['model'])} | {row['passed']}/{row['ran']}"
                f"{skipped} | {totals.input_tokens:,} | "
                f"{totals.cache_read_tokens:,} | {totals.output_tokens:,} | {_pct(totals.cache_read_share)} | "
                f"{_usd(totals.cost_usd, totals.cost_source)} |"
            )
        lines.append("")
    if report.tasks:
        lines += [
            "## Tasks",
            "",
            "| Provider | Task | Result | Calls | Input | Cache read | 1h writes | Output (reasoning) | "
            "Cache share | First-call share | TTFT ms | Cost | Prefix breaks | Reason |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for task in report.tasks:
            totals = task.totals
            # A1's DEBUG diff names every request; no entry at all means it did not run for this task.
            diffed = any(key.startswith("prefix_") for key in task.log_counts)
            breaks = task.log_counts.get("prefix_broken", 0) if diffed else None
            lines.append(
                f"| {_cell(task.provider)} | {_cell(task.task_id)} | {task.status} | {totals.calls} | "
                f"{totals.input_tokens:,} | {totals.cache_read_tokens:,} | {totals.cache_write_1h_tokens:,} | "
                f"{totals.output_tokens:,} ({totals.reasoning_tokens:,}) | {_pct(totals.cache_read_share)} | "
                f"{_pct(task.first_call_cache_read_share)} | {totals.ttft_ms_first if totals.ttft_ms_first is not None else ''} | "
                f"{_usd(totals.cost_usd, totals.cost_source)} | {'' if breaks is None else breaks} | {_cell(task.reason)} |"
            )
        lines.append("")
        noted = [task for task in report.tasks if any(
            task.log_counts.get(key) for key in ("thinking_dropped_logs", "thinking_signature_retries",
                                                  "thinking_controls_rejected", "cache_marker_retries",
                                                  "reasoning_replay_retries", "reasoning_fallbacks",
                                                  "output_truncated", "background_schema_refused")
        )]
        if noted:
            lines += ["### Recoveries and drops seen in the logs", ""]
            for task in noted:
                counts = ", ".join(f"{key}={value}" for key, value in sorted(task.log_counts.items())
                                   if not key.startswith("prefix_") and value)
                lines.append(f"- {task.provider}/{task.task_id}: {counts}")
            lines.append("")
    if report.probes:
        lines += ["## Verification checks", "", "| Check | Provider | Model | Result | Observed | Note |", "|---|---|---|---|---|---|"]
        for probe in report.probes:
            observed = json.dumps(probe.observed, sort_keys=True, default=str)
            lines.append(
                f"| {_cell(probe.check)} | {_cell(probe.provider)} | {_cell(probe.model)} | {probe.status} | "
                f"{_cell(observed[:400])} | {_cell(probe.note)} |"
            )
        lines.append("")
    if manual:
        lines += ["## Manual checks (not automated)", ""]
        for item in manual:
            lines.append(f"- **{item.check_id}** ({item.source}): {item.steps} Expect: {item.expect}")
        lines.append("")
    return "\n".join(lines)


def write_report(report: Report, folder: Path, *, rates: Mapping[str, Rates] | None = None, manual: Sequence[Any] = ()) -> tuple[Path, Path]:
    """Write ``report.json`` and ``report.md`` (LF endings) into *folder*."""
    folder.mkdir(parents=True, exist_ok=True)
    json_path = folder / "report.json"
    md_path = folder / "report.md"
    data = report.to_dict()
    json_path.write_bytes(redact(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
    report.generated_at = data["generated_at"]
    md_path.write_bytes(redact(render_markdown(report, rates=rates, manual=manual) + "\n").encode("utf-8"))
    return json_path, md_path


# ── A/B diff ───────────────────────────────────────────────────────────────


@dataclass
class TaskDiff:
    provider: str
    task_id: str
    status_a: str
    status_b: str
    input_a: int
    input_b: int
    cache_share_a: float | None
    cache_share_b: float | None
    first_share_a: float | None
    first_share_b: float | None
    output_a: int
    output_b: int
    cost_a: float | None
    cost_b: float | None


def _mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def diff_reports(a: Report, b: Report) -> dict[str, Any]:
    """Compare two runs task by task (matched on provider and task id)."""
    left = {(task.provider, task.task_id): task for task in a.tasks}
    right = {(task.provider, task.task_id): task for task in b.tasks}
    rows: list[TaskDiff] = []
    for key in sorted(set(left) & set(right)):
        x, y = left[key], right[key]
        rows.append(TaskDiff(
            provider=key[0], task_id=key[1], status_a=x.status, status_b=y.status,
            input_a=x.totals.input_tokens, input_b=y.totals.input_tokens,
            cache_share_a=x.totals.cache_read_share, cache_share_b=y.totals.cache_read_share,
            first_share_a=x.first_call_cache_read_share, first_share_b=y.first_call_cache_read_share,
            output_a=x.totals.output_tokens, output_b=y.totals.output_tokens,
            cost_a=x.totals.cost_usd, cost_b=y.totals.cost_usd,
        ))

    def side(report: Report, keys: set) -> dict[str, Any]:
        tasks = [task for task in report.tasks if (task.provider, task.task_id) in keys]
        ran = [task for task in tasks if task.status in {PASS, FAIL, ERROR}]
        costs = [task.totals.cost_usd for task in tasks]
        # The first call of the first task of each provider has nothing warm to read: leave it out.
        firsts: list[float | None] = []
        seen: set[str] = set()
        for task in tasks:
            if task.provider in seen:
                firsts.append(task.first_call_cache_read_share)
            seen.add(task.provider)
        return {
            "variant": report.variant,
            "passed": sum(task.status == PASS for task in tasks),
            "ran": len(ran),
            "input_tokens": sum(task.totals.input_tokens for task in tasks),
            "cache_read_tokens": sum(task.totals.cache_read_tokens for task in tasks),
            "output_tokens": sum(task.totals.output_tokens for task in tasks),
            "cost_usd": None if any(cost is None for cost in costs) else sum(c for c in costs if c is not None),
            "mean_cache_read_share": _mean(task.totals.cache_read_share for task in tasks),
            "mean_first_call_cache_read_share": _mean(firsts),
        }

    shared = set(left) & set(right)
    return {
        "a": side(a, shared),
        "b": side(b, shared),
        "only_a": sorted(f"{p}/{t}" for p, t in set(left) - set(right)),
        "only_b": sorted(f"{p}/{t}" for p, t in set(right) - set(left)),
        "tasks": [asdict(row) for row in rows],
    }


def render_diff_markdown(diff: Mapping[str, Any]) -> str:
    a, b = diff["a"], diff["b"]
    lines = [
        f"# Live eval A/B: `{a['variant']}` vs `{b['variant']}`",
        "",
        "| | A | B |",
        "|---|---|---|",
        f"| Passed | {a['passed']}/{a['ran']} | {b['passed']}/{b['ran']} |",
        f"| Input tokens | {a['input_tokens']:,} | {b['input_tokens']:,} |",
        f"| Cache-read tokens | {a['cache_read_tokens']:,} | {b['cache_read_tokens']:,} |",
        f"| Output tokens | {a['output_tokens']:,} | {b['output_tokens']:,} |",
        f"| Cost | {_usd(a['cost_usd'])} | {_usd(b['cost_usd'])} |",
        f"| Mean cache-read share | {_pct(a['mean_cache_read_share'])} | {_pct(b['mean_cache_read_share'])} |",
        f"| Mean first-call cache-read share (new conversations) | {_pct(a['mean_first_call_cache_read_share'])} | "
        f"{_pct(b['mean_first_call_cache_read_share'])} |",
        "",
        "| Provider | Task | A | B | Input A→B | Cache share A→B | First-call share A→B | Cost A→B |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in diff["tasks"]:
        lines.append(
            f"| {_cell(row['provider'])} | {_cell(row['task_id'])} | {row['status_a']} | {row['status_b']} | "
            f"{row['input_a']:,}→{row['input_b']:,} | {_pct(row['cache_share_a'])}→{_pct(row['cache_share_b'])} | "
            f"{_pct(row['first_share_a'])}→{_pct(row['first_share_b'])} | {_usd(row['cost_a'])}→{_usd(row['cost_b'])} |"
        )
    for label, key in (("Only in A", "only_a"), ("Only in B", "only_b")):
        if diff[key]:
            lines += ["", f"{label}: {', '.join(diff[key])}"]
    return "\n".join(lines) + "\n"
