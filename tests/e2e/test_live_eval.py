"""Opt-in live eval: representative tasks through the real Row-Bot Agent runtime, per provider.

Each provider runs in its own worker process with a fresh data folder inside
the run folder (``.tmp/live-eval/<timestamp>/``), keys from the environment
only, and the OS keychain closed. The run stops before it would pass
``ROW_BOT_LIVE_EVAL_MAX_USD`` / ``ROW_BOT_LIVE_EVAL_MAX_TOKENS``. Every task is
recorded with pass/fail, the usage ledger's tokens (cache reads and 5m/1h
writes, reasoning), the cache-read share, latency/TTFT and cost (OpenRouter's
own figure, else the rates file in ``ROW_BOT_LIVE_EVAL_RATES``, else
"unpriced"). The report is ``report.json`` and ``report.md`` in the run folder.

    ROW_BOT_LIVE_EVAL=1 ANTHROPIC_API_KEY=... uv run python -m pytest tests/e2e/test_live_eval.py -m live_provider

``uv run python scripts/live_eval.py plan`` lists tasks x providers and the
estimated tokens without any network call. The tasks are in
``tests/e2e/live_eval/tasks.py``; the manual checks that cannot be automated
(desktop chart, Slack/Discord threads, albums, MCP over 24K characters, a 32K
local model in Agent mode, upgrading with existing threads, OAuth providers)
are in ``tests/e2e/live_eval/manual.py``.
"""
from __future__ import annotations

import os
from dataclasses import asdict
from typing import Iterator

import pytest

from tests.e2e.live_eval import orchestrate, providers as provider_registry
from tests.e2e.live_eval.budget import BudgetGuard, Caps
from tests.e2e.live_eval.manual import MANUAL_CHECKS
from tests.e2e.live_eval.pricing import RATES_ENV, load_rates
from tests.e2e.live_eval.report import BUDGET, ERROR, PASS, Report, TaskRecord, write_report
from tests.e2e.live_eval.tasks import select_tasks

pytestmark = pytest.mark.live_provider

MIN_PASS_ENV = "ROW_BOT_LIVE_EVAL_MIN_PASS_RATE"
DEFAULT_MIN_PASS = 0.8


class _Run:
    def __init__(self) -> None:
        self.folder = orchestrate.new_run_dir()
        self.guard = BudgetGuard(Caps.from_env())
        rates_path = os.environ.get(RATES_ENV, "")
        self.rates = load_rates(rates_path) if rates_path else {}
        self.report = Report(run_id=self.folder.name, caps={"describe": self.guard.caps.describe()})

    def finish(self) -> None:
        self.report.spent = asdict(self.guard.spent)
        self.report.probes = orchestrate.read_probes(self.folder)
        write_report(self.report, self.folder, rates=self.rates, manual=MANUAL_CHECKS)
        self.guard.save(self.folder / "budget.json")


@pytest.fixture(scope="module")
def live_run() -> Iterator[_Run]:
    if not orchestrate.enabled():
        pytest.skip(f"set {orchestrate.ENABLE_ENV}=1 to run real provider calls (they cost money)")
    run = _Run()
    yield run
    run.finish()


@pytest.mark.parametrize("provider_id", [item.provider_id for item in provider_registry.TARGETS])
def test_live_eval_provider(provider_id: str, live_run: _Run) -> None:
    wanted = [name.strip() for name in os.environ.get(provider_registry.PROVIDERS_ENV, "").split(",") if name.strip()]
    if wanted and provider_id not in wanted:
        pytest.skip(f"not in {provider_registry.PROVIDERS_ENV}")
    state = provider_registry.availability(provider_registry.target(provider_id))
    if not state.available:
        live_run.report.providers.append({"provider": provider_id, "status": "skipped", "reason": state.reason})
        pytest.skip(state.reason)
    task_ids = [task.task_id for task in select_tasks(provider_id)]
    result = orchestrate.run_provider(state, task_ids, live_run.folder, live_run.guard,
                                      variant_text=os.environ.get("ROW_BOT_LIVE_EVAL_VARIANT", ""))
    live_run.report.providers.append({
        "provider": provider_id, "model": state.model, "status": result.get("status"),
        "reason": result.get("reason", ""), "setup": result.get("setup_notes", []),
        "credentials": result.get("credentials", ""),
    })
    records = [TaskRecord.from_dict(task).finish(live_run.rates) for task in result.get("tasks") or []]
    live_run.report.tasks.extend(records)
    if result.get("status") == "skipped":
        pytest.skip(result.get("reason", "skipped"))
    assert result.get("status") == "ran", f"{provider_id} worker: {result.get('reason')}\n{result.get('stderr_tail', '')}"
    errors = [f"{record.task_id}: {record.reason or record.errors[:1]}" for record in records if record.status == ERROR]
    ran = [record for record in records if record.status not in {BUDGET, "skipped"}]
    passed = sum(record.status == PASS for record in ran)
    minimum = float(os.environ.get(MIN_PASS_ENV, "") or DEFAULT_MIN_PASS)
    if not ran and any(record.status == BUDGET for record in records):
        pytest.skip("the budget cap stopped the run before this provider's tasks")
    assert not errors, f"runtime errors (not model mistakes): {errors}"
    assert passed >= minimum * len(ran), (
        f"{provider_id}: {passed}/{len(ran)} tasks passed (< {minimum:.0%}); see {live_run.folder / 'report.md'}"
    )
