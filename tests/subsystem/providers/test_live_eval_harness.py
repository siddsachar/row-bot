"""The opt-in live eval harness (WP B3) behaves without any provider: budget, report, tasks, plan.

The live tests themselves are in ``tests/e2e`` (``live_provider``); these cover
the parts that decide whether a live run may spend, what it records and what
it writes, with fakes and no network.
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage

import row_bot.agent as agent  # once, at collection: several tests below drive or patch it
from tests.e2e.live_eval import orchestrate, providers as provider_registry, variants
from tests.e2e.live_eval.budget import (
    BudgetConfigError,
    BudgetExceeded,
    BudgetGuard,
    Caps,
    Spend,
    estimate_call,
)
from tests.e2e.live_eval.capture import HttpCapture
from tests.e2e.live_eval.fixtures import FixtureServer
from tests.e2e.live_eval.ledger import everyday_data_folder, max_id, rows_after
from tests.e2e.live_eval.manual import MANUAL_CHECKS
from tests.e2e.live_eval.pricing import CallTokens, Rates, load_rates, price_call, rates_for, upper_bound_usd
from tests.e2e.live_eval.report import (
    CallRecord,
    ProbeRecord,
    Report,
    TaskRecord,
    diff_reports,
    render_diff_markdown,
    summarize_calls,
    write_report,
)
from tests.e2e.live_eval.tasks import (
    ARTICLE_CODEWORD,
    TASKS,
    Context,
    Outcome,
    ToolCall,
    TurnResult,
    by_id,
    estimate_tokens,
    select_tasks,
)

pytestmark = pytest.mark.subsystem


def _record_usage(data_dir: Path, monkeypatch: pytest.MonkeyPatch, **counts: int | float | None) -> None:
    from row_bot.prompt_cache import ModelUsage
    from row_bot.providers.usage_ledger import UsageRecord, record_model_usage

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    usage = ModelUsage(**{key: value for key, value in counts.items() if key != "thread"})
    assert record_model_usage(UsageRecord(purpose="main", provider="anthropic", model="claude-test", usage=usage,
                                          thread_id=str(counts.get("thread") or "t1")))


# ── Budget ──────────────────────────────────────────────────────────────────


def test_caps_come_from_the_environment_and_never_both_off() -> None:
    assert Caps.from_env({}) == Caps(5.0, 2_000_000)
    assert Caps.from_env({"ROW_BOT_LIVE_EVAL_MAX_USD": "1.5", "ROW_BOT_LIVE_EVAL_MAX_TOKENS": "off"}) == Caps(1.5, None)
    with pytest.raises(BudgetConfigError):
        Caps.from_env({"ROW_BOT_LIVE_EVAL_MAX_USD": "off", "ROW_BOT_LIVE_EVAL_MAX_TOKENS": "off"})
    with pytest.raises(BudgetConfigError):
        Caps.from_env({"ROW_BOT_LIVE_EVAL_MAX_USD": "-2"})
    with pytest.raises(BudgetConfigError):
        Caps.from_env({"ROW_BOT_LIVE_EVAL_MAX_TOKENS": "lots"})


def test_the_guard_refuses_what_would_pass_a_cap_before_it_runs() -> None:
    guard = BudgetGuard(Caps(max_usd=1.0, max_tokens=1_000))
    assert guard.over_by(900) == ""
    guard.charge(CallTokens(input=600, output=100), price_call(CallTokens(input=600, output=100, cost=0.4), None))
    assert guard.spent == Spend(tokens=700, usd=0.4, unpriced_tokens=0, calls=1)
    assert "token cap" in guard.over_by(301)
    assert guard.over_by(300) == ""
    assert "USD cap" in guard.over_by(10, 0.7)
    with pytest.raises(BudgetExceeded):
        guard.require(301, what="task")
    assert guard.remaining() == Caps(max_usd=pytest.approx(0.6), max_tokens=300)


def test_unpriced_calls_are_held_by_the_token_cap_and_by_priced_spend_already_made() -> None:
    guard = BudgetGuard(Caps(max_usd=0.5, max_tokens=None))
    guard.charge(CallTokens(input=100, output=10), price_call(CallTokens(input=100, output=10), None))
    assert guard.spent.unpriced_tokens == 110
    assert guard.over_by(10_000_000) == ""  # nothing priced yet, no token cap: only the USD cap applies
    guard.charge(CallTokens(input=1, output=1, cost=0.5), price_call(CallTokens(input=1, output=1, cost=0.5), None))
    assert "USD cap" in guard.over_by(1)  # an unpriced call cannot start once priced spend reached the cap


def test_a_saved_guard_resumes_with_its_spend(tmp_path: Path) -> None:
    guard = BudgetGuard(Caps(2.0, 50_000), Spend(tokens=10, usd=0.1, unpriced_tokens=3, calls=2))
    guard.save(tmp_path / "budget.json")
    assert BudgetGuard.load(tmp_path / "budget.json", Caps()).to_dict() == guard.to_dict()
    assert BudgetGuard.load(tmp_path / "missing.json", Caps(1.0, 5)).caps == Caps(1.0, 5)


def test_call_estimates_lean_high_and_count_tools_and_output() -> None:
    text = "x" * 3_500
    tokens_in, tokens_out = estimate_call([HumanMessage(content=text)], max_output=200)
    assert tokens_in >= 1_000 and tokens_out == 200
    with_tools, _ = estimate_call([{"role": "user", "content": text}], tools=[{"name": "t", "description": "d" * 700}])
    assert with_tools > tokens_in
    _, default_out = estimate_call([HumanMessage(content="hi")])
    assert default_out == 4_096
    _, capped_out = estimate_call([HumanMessage(content="hi")], max_output=1_000_000)
    assert capped_out == 16_384


def test_the_call_gate_stops_a_model_call_before_it_is_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    from row_bot.providers.usage_ledger import attach_usage_ledger
    from tests.e2e.live_eval.runner import BudgetGate, install_budget_gate

    data_dir = tmp_path / "data"
    _record_usage(data_dir, monkeypatch, input_tokens=900, output_tokens=50)
    replies = iter([AIMessage(content="sent")])
    model = attach_usage_ledger(GenericFakeChatModel(messages=replies), provider_id="fake", model_name="fake")
    gate = BudgetGate(Caps(None, 1_000 + 4_200), {}, lambda: data_dir / "usage.db")
    undo = install_budget_gate(gate)
    try:
        with pytest.raises(BudgetExceeded):
            model.invoke([HumanMessage(content="y" * 2_000)])
        assert gate.refused == 1
        assert next(replies).content == "sent"  # the fake provider never answered: the call was not sent
    finally:
        undo()
    from row_bot.providers.usage_ledger import UsageLedgerCallback

    assert UsageLedgerCallback.raise_error is False


# ── Pricing ─────────────────────────────────────────────────────────────────


def test_costs_come_from_the_provider_then_the_rates_file_else_unpriced(tmp_path: Path) -> None:
    rates_file = tmp_path / "rates.json"
    rates_file.write_text(json.dumps({"models": {
        "anthropic:claude-test": {"input": 4, "output": 20, "cache_read": 0.4, "cache_write_5m": 5, "cache_write_1h": 8},
        "openai:*": {"input": 1, "output": 2},
    }}), encoding="utf-8")
    table = load_rates(rates_file)
    claude = rates_for(table, "anthropic", "claude-test")
    tokens = CallTokens(input=1_000_000, cache_read=600_000, cache_write=300_000, cache_write_1h=200_000, output=100_000)
    price = price_call(tokens, claude)
    expected = 100_000 * 4 + 600_000 * 0.4 + 100_000 * 5 + 200_000 * 8 + 100_000 * 20
    assert price.source == "rates" and price.usd == pytest.approx(expected / 1_000_000)
    assert price_call(CallTokens(input=10, output=1, cost=0.25), claude).source == "provider"
    assert rates_for(table, "openai", "gpt-anything") == Rates(1, 2)
    cached = price_call(CallTokens(input=100, cache_read=50, output=1), rates_for(table, "openai", "x"))
    assert cached.usd is None and "cache_read" in cached.reason
    assert price_call(CallTokens(input=1, output=1), None).source == "unpriced"
    assert price_call(CallTokens(), claude).reason == "the provider reported no counts"
    assert load_rates("") == {}


def test_the_upper_bound_uses_the_dearest_input_side_rate() -> None:
    assert upper_bound_usd(1_000_000, 0, Rates(input=4, output=20, cache_write_1h=8)) == pytest.approx(8)
    assert upper_bound_usd(1_000_000, 1_000_000, None) is None


# ── Report ──────────────────────────────────────────────────────────────────


def _call(**values) -> CallRecord:
    return CallRecord(provider="anthropic", model="claude-test", **values)


def test_totals_count_cache_share_only_where_reported_and_find_bursts() -> None:
    calls = [
        _call(purpose="main", input_tokens=1_000, cache_read_tokens=0, output_tokens=10, created_at=0.0, thread="a"),
        _call(purpose="main", input_tokens=1_000, cache_read_tokens=900, output_tokens=10, created_at=10.0, thread="a"),
        _call(purpose="compaction", input_tokens=500, cache_read_tokens=None, output_tokens=5, created_at=70.5, thread="a"),
        _call(purpose="main", input_tokens=None, output_tokens=None, created_at=11.0, thread="b"),
    ]
    totals = summarize_calls(calls)
    assert totals.cache_read_share == pytest.approx(900 / 2_000)
    assert totals.calls_without_counts == 1
    assert totals.cost_usd is None and totals.cost_source == "unpriced"
    assert totals.max_calls_per_minute == 2
    record = TaskRecord("t", "chat", "anthropic", "claude-test", calls=calls).finish()
    assert record.first_call_cache_read_share == 0.0
    assert record.by_purpose == {"main": 3, "compaction": 1}


def test_the_report_is_written_as_json_and_markdown(tmp_path: Path) -> None:
    record = TaskRecord("calc_single", "tools", "anthropic", "claude-test", status="pass",
                        calls=[_call(purpose="main", input_tokens=2_000, cache_read_tokens=1_500, cache_write_tokens=400,
                                     cache_write_1h_tokens=400, output_tokens=20, cost=None)],
                        log_counts={"prefix_broken": 1, "thinking_dropped_logs": 2}).finish()
    report = Report(run_id="run-1", caps={"describe": "$5.00 priced"}, spent={"tokens": 2_020, "usd": 0.0},
                    tasks=[record], probes=[ProbeRecord("b1_usage_shapes", "anthropic", "claude-test", "pass", {"rows": 2})],
                    notes=["a note"])
    json_path, md_path = write_report(report, tmp_path, manual=MANUAL_CHECKS[:1])
    assert b"\r\n" not in json_path.read_bytes() and b"\r\n" not in md_path.read_bytes()
    loaded = Report.load(json_path)
    assert loaded.tasks[0].totals.cache_write_1h_tokens == 400 and loaded.probes[0].observed == {"rows": 2}
    markdown = md_path.read_text(encoding="utf-8")
    for expected in ("calc_single", "75%", "unpriced", "b1_usage_shapes", "thinking_dropped_logs=2", MANUAL_CHECKS[0].check_id):
        assert expected in markdown


def test_reports_never_keep_a_key_a_provider_error_echoed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.e2e.live_eval.report import redact

    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-live-eval-test-value-0123")
    leaked = ("401: invalid key anthropic-live-eval-test-value-0123; Incorrect API key provided: "
              "sk-proj-****************************abcd; header Bearer abcdefghijklmnopqrstu; AIza" + "x" * 35)
    cleaned = redact(leaked)
    for secret in ("anthropic-live-eval-test-value-0123", "sk-proj-", "abcdefghijklmnopqrstu", "AIzaxxx"):
        assert secret not in cleaned
    assert redact("skills_config.json and xai_oauth stay readable") == "skills_config.json and xai_oauth stay readable"
    record = TaskRecord("chat_exact", "chat", "anthropic", "m", status="error", errors=[leaked], reason=leaked).finish()
    json_path, md_path = write_report(Report("run", tasks=[record]), tmp_path)
    for path in (json_path, md_path):
        assert "anthropic-live-eval-test-value-0123" not in path.read_text(encoding="utf-8")


def test_ab_diff_matches_tasks_and_ignores_each_providers_first_cold_call() -> None:
    def task(task_id: str, first_share: float, status: str = "pass") -> TaskRecord:
        calls = [_call(purpose="main", input_tokens=1_000, cache_read_tokens=int(first_share * 1_000), output_tokens=5)]
        return TaskRecord(task_id, "chat", "openai", "gpt-test", status=status, calls=calls).finish()

    a = Report("a", variant="baseline", tasks=[task("one", 0.0), task("two", 0.2), task("three", 0.4)])
    b = Report("b", variant="cache-key-none", tasks=[task("one", 0.0), task("two", 0.8, "fail"), task("four", 0.9)])
    diff = diff_reports(a, b)
    assert [row["task_id"] for row in diff["tasks"]] == ["one", "two"]
    assert diff["a"]["mean_first_call_cache_read_share"] == pytest.approx(0.2)
    assert diff["b"]["mean_first_call_cache_read_share"] == pytest.approx(0.8)
    assert diff["b"]["passed"] == 1 and diff["only_a"] == ["openai/three"] and diff["only_b"] == ["openai/four"]
    assert "cache-key-none" in render_diff_markdown(diff)


# ── Tasks ───────────────────────────────────────────────────────────────────


def test_the_registry_holds_twenty_to_thirty_distinct_tasks_across_the_planned_areas() -> None:
    ids = [task.task_id for task in TASKS]
    assert 20 <= len(ids) <= 30 and len(ids) == len(set(ids))
    assert {"chat", "tools", "files", "web", "memory", "workflow", "delegation", "long_thread", "local", "cache"} <= {
        task.category for task in TASKS}
    known = by_id()
    assert all(not task.depends_on or task.depends_on in known for task in TASKS)


def test_task_selection_follows_tier_filter_and_provider() -> None:
    core = [task.task_id for task in select_tasks("anthropic", {})]
    full = [task.task_id for task in select_tasks("anthropic", {"ROW_BOT_LIVE_EVAL_TIER": "full"})]
    assert "read_url_paged" not in core and "read_url_paged" in full
    assert not {"ollama_local_turn", "ollama_no_reload"} & set(full)
    local = {task.task_id for task in select_tasks("ollama", {})}
    assert {"ollama_local_turn", "ollama_no_reload", "chat_exact"} <= local and "workflow_run" not in local
    assert [t.task_id for t in select_tasks("openai", {"ROW_BOT_LIVE_EVAL_TASKS": "calc_single,chat_exact"})] == [
        "chat_exact", "calc_single"]
    with pytest.raises(ValueError):
        select_tasks("openai", {"ROW_BOT_LIVE_EVAL_TASKS": "no_such_task"})
    with pytest.raises(ValueError):
        select_tasks("openai", {"ROW_BOT_LIVE_EVAL_TIER": "huge"})


def _outcome(*answers: str, workspace: Path | None = None, tools: tuple[str, ...] = (), **collected) -> Outcome:
    turns = [TurnResult(prompt="p", answer=answer, status="done") for answer in answers]
    if turns and tools:
        turns[0].tool_calls = [ToolCall(name) for name in tools]
    return Outcome(turns=turns, workspace=workspace, collected=dict(collected))


def test_checks_decide_from_the_answer_files_and_collected_facts(tmp_path: Path) -> None:
    context = Context()
    tasks = by_id()
    assert tasks["chat_exact"].check(_outcome("**Live eval OK.**"), context).passed
    assert not tasks["chat_exact"].check(_outcome("live eval ok, and more"), context).passed
    assert tasks["calc_single"].check(_outcome("The answer is 5,202.", tools=("calculate",)), context).passed
    assert not tasks["calc_single"].check(_outcome("52021", tools=("calculate",)), context).passed
    assert not tasks["calc_single"].check(_outcome("5202"), context).passed  # no tool call
    assert tasks["chat_json"].check(_outcome('```json\n{"city": "Tokyo", "country": "Japan"}\n```'), context).passed
    assert not tasks["chat_json"].check(_outcome("Tokyo, Japan"), context).passed
    (tmp_path / "settings.ini").write_text("[server]\nhost = localhost\nport = 8085\nmode = prod\n", encoding="utf-8")
    assert tasks["file_edit_ini"].check(_outcome("done", workspace=tmp_path), context).passed
    (tmp_path / "settings.ini").write_text("[server]\nport = 8085\n", encoding="utf-8")
    assert not tasks["file_edit_ini"].check(_outcome("done", workspace=tmp_path), context).passed
    assert tasks["read_url_local"].check(_outcome(ARTICLE_CODEWORD, tools=("read_url",)), context).passed
    assert not tasks["memory_save"].check(_outcome("Saved.", memory_has_fact=False), context).passed
    assert tasks["memory_save"].check(_outcome("Saved.", memory_has_fact=True), context).passed
    failed = Outcome(turns=[TurnResult(prompt="p", status="error", errors=["boom"])])
    assert "did not finish" in tasks["chat_exact"].check(failed, context).reason


def test_the_long_thread_needs_its_keywords_and_a_compaction() -> None:
    from tests.e2e.live_eval.tasks import chapter_word

    context = Context(long_turns=4)
    task = by_id()["long_thread_compaction"]
    turns = task.turns(context)
    assert len(turns) == 5 and "chapters/chapter_1.txt" in turns[0].prompt and chapter_word(1) in turns[1].prompt
    words = [chapter_word(index) for index in range(4)]
    answers = [*words, ", ".join(words)]
    assert task.check(_outcome(*answers, compaction_calls=1), context).passed
    assert "compactions 0" in task.check(_outcome(*answers, compaction_calls=0), context).reason
    assert not task.check(_outcome(*answers[:-1], ", ".join(words[:2]), compaction_calls=1), context).passed


def test_estimates_grow_with_calls_and_the_long_thread_is_held_to_its_window() -> None:
    tasks = by_id()
    assert estimate_tokens(tasks["warm_loop_cache"], Context()) > estimate_tokens(tasks["calc_single"], Context())
    small = estimate_tokens(tasks["long_thread_compaction"], Context(long_window=32_000))
    large = estimate_tokens(tasks["long_thread_compaction"], Context(long_window=200_000))
    assert small < large


# ── Providers and the plan ──────────────────────────────────────────────────


def test_providers_are_available_from_environment_keys_only() -> None:
    from row_bot.providers.auth_store import PROVIDER_API_KEY_ENV

    targets = {item.provider_id: item.key_env for item in provider_registry.TARGETS if item.key_env}
    assert targets == dict(PROVIDER_API_KEY_ENV)  # every API-key provider Row-Bot knows can run
    selection = provider_registry.select({"ANTHROPIC_API_KEY": "k", "ROW_BOT_LIVE_EVAL_MODEL_ANTHROPIC": "claude-x"})
    assert [(s.target.provider_id, s.model) for s in selection.chosen] == [("anthropic", "claude-x")]
    assert "OPENAI_API_KEY" in next(s.reason for s in selection.skipped if s.target.provider_id == "openai")
    ollama = provider_registry.availability(provider_registry.target("ollama"), {"ROW_BOT_LIVE_EVAL_OLLAMA_MODEL": "qwen3:8b"})
    assert ollama.available and ollama.needs_probe
    chosen = provider_registry.select({"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k", "ROW_BOT_LIVE_EVAL_PROVIDERS": "openai"})
    assert [s.target.provider_id for s in chosen.chosen] == ["openai"]
    with pytest.raises(ValueError, match="OAuth-only"):
        provider_registry.select({"ROW_BOT_LIVE_EVAL_PROVIDERS": "xai_oauth"})


def test_the_dry_run_plans_tasks_by_provider_without_spending(monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    env = {"ANTHROPIC_API_KEY": "placeholder", "OPENROUTER_API_KEY": "placeholder", "ROW_BOT_LIVE_EVAL_MAX_TOKENS": "100000"}
    plan = orchestrate.build_plan(env)
    assert [row["provider"] for row in plan["providers"]] == ["anthropic", "openrouter"]
    anthropic = plan["providers"][0]
    assert len(anthropic["tasks"]) >= 20 and anthropic["tokens"] == sum(item["tokens"] for item in anthropic["tasks"])
    assert plan["fits"] is False and "token cap" in plan["over"]
    assert any(check["check"] == "a2b_controls_accepted" and check["providers"] == ["anthropic"] for check in plan["checks"])

    def no_subprocess(*_args, **_kwargs):
        raise AssertionError("planning must not start a worker")

    monkeypatch.setattr(orchestrate.subprocess, "run", no_subprocess)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    # The CLI writes its flags into the environment; let monkeypatch put them back afterwards.
    monkeypatch.setenv(provider_registry.PROVIDERS_ENV, "")
    monkeypatch.delenv(orchestrate.ENABLE_ENV, raising=False)
    assert orchestrate.main(["plan"]) == 0
    assert "Tasks x providers" in capsys.readouterr().out
    assert orchestrate.main(["run", "--providers", "anthropic"]) == 2  # not enabled: prints the plan, spends nothing
    assert f"Set {orchestrate.ENABLE_ENV}=1" in capsys.readouterr().out
    assert orchestrate.main(["run", "--dry-run"]) == 0


def test_a_worker_gets_its_own_data_folder_and_a_closed_keychain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    env = orchestrate.child_env(tmp_path / "data", tmp_path / "ws")
    assert env["ROW_BOT_DATA_DIR"] == str(tmp_path / "data")
    assert env["ROW_BOT_TEST_MODE"] == "true" and "PYTEST_XDIST_WORKER" not in env
    assert env["ROW_BOT_DESKTOP_NOTIFICATIONS"] == "off"  # a failed eval turn never reaches the desktop
    from tests.e2e.live_eval import worker

    home = tmp_path / "home"  # a stand-in home: the real everyday folder is never named, even briefly
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(home / ".row-bot"))
    with pytest.raises(SystemExit):
        worker.main(["--provider", "anthropic", "--model", "m", "--tasks", "chat_exact", "--out", str(tmp_path / "o.json"),
                     "--workspace", str(tmp_path / "ws")])
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ROW_BOT_TEST_MODE", raising=False)
    with pytest.raises(SystemExit):
        worker.main(["--provider", "anthropic", "--model", "m", "--tasks", "chat_exact", "--out", str(tmp_path / "o.json"),
                     "--workspace", str(tmp_path / "ws")])
    assert not (tmp_path / "o.json").exists()


def test_the_ledger_command_reads_counts_and_refuses_the_everyday_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                          capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    assert everyday_data_folder(home / ".row-bot" / "sub") and everyday_data_folder(home / ".thoth")
    assert not everyday_data_folder(tmp_path / "validation")
    assert max_id(tmp_path / "missing" / "usage.db") == 0 and not (tmp_path / "missing").exists()
    _record_usage(tmp_path, monkeypatch, input_tokens=100, cache_read_tokens=80, output_tokens=5)
    _record_usage(tmp_path, monkeypatch, input_tokens=200, output_tokens=7, thread="t2")
    assert max_id(tmp_path / "usage.db") == 2
    rows = rows_after(tmp_path / "usage.db", 1)
    assert len(rows) == 1 and rows[0].input_tokens == 200 and rows[0].thread == "t2"
    assert orchestrate.main(["ledger", "--data-dir", str(tmp_path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["calls"] == 2 and summary["groups"][0]["input_tokens"] == 300
    assert orchestrate.main(["ledger", "--data-dir", str(home / ".row-bot")]) == 2


# ── Variants ────────────────────────────────────────────────────────────────


def test_variant_strings_parse_and_reject_unknowns() -> None:
    assert variants.parse("") == variants.Variant()
    parsed = variants.parse("cache-key-none,a4a-jump-share=0.1,headroom=1024,patch:row_bot.history_hygiene:MASK_MIN_TOKENS=64")
    assert parsed.hooks == ("cache-key-none", "headroom=1024")
    assert {(p.attribute, p.value) for p in parsed.patches} == {("JUMP_SHARE", 0.1), ("MASK_MIN_TOKENS", 64)}
    for bad in ("cache-key-none,cache-key-install", "warp-speed", "patch:os:sep=1", "a4a-jump-share=lots", "headroom=-1"):
        with pytest.raises(variants.VariantError):
            variants.parse(bad)


def test_cache_key_variants_change_what_providers_are_sent_and_restore() -> None:
    from row_bot.providers import cache_affinity

    token = agent._current_thread_id_var.set("thread-one")
    try:
        shipped = cache_affinity.conversation_cache_key("openai")
        applied = variants.apply(variants.parse("cache-key-none"))
        try:
            assert cache_affinity.conversation_cache_key("openai") is None
        finally:
            applied.restore()
        assert cache_affinity.conversation_cache_key("openai") == shipped
        applied = variants.apply(variants.parse("cache-key-install"))
        try:
            first = cache_affinity.conversation_cache_key("openai")
            agent._current_thread_id_var.set("thread-two")
            assert cache_affinity.conversation_cache_key("openai") == first
        finally:
            applied.restore()
    finally:
        agent._current_thread_id_var.reset(token)


def test_tuning_variants_reach_the_values_row_bot_reads_and_restore() -> None:
    from row_bot import history_hygiene
    from row_bot.providers import background
    from row_bot.providers.transports import thinking_binding

    before = history_hygiene.jump_threshold(100_000)
    applied = variants.apply(variants.parse("a4a-jump-share=0.1,a4a-keep-turns=2,headroom=512,thinking-error"))
    try:
        assert history_hygiene.jump_threshold(100_000) == 10_000
        people = [HumanMessage(content=str(index)) for index in range(4)]
        assert history_hygiene.protected_start(people) == 2
        payload = thinking_binding.with_thinking_binding({"model": "claude-opus-5-5", "messages": []})
        assert payload["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "error"}
        assert background.background_reasoning("model:custom_openai_x:y").headroom == 512
    finally:
        applied.restore()
    assert history_hygiene.jump_threshold(100_000) == before
    assert thinking_binding.with_thinking_binding({"model": "claude-opus-5-5", "messages": []})["thinking"][
        "block_binding"] == {"prefix_mismatch_behavior": "drop_block"}


# ── Capture, fixtures and the runner's pieces ───────────────────────────────


def test_the_capture_keeps_bodies_and_never_credentials() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stream":
            return httpx.Response(200, content=b'data: {"type": "message_start"}\n\ndata: [DONE]\n\n',
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"ok": True, "usage": {"input_tokens": 3}})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    with HttpCapture(lambda request: "example.test" in str(request.url)) as capture:
        client.post("https://api.example.test/v1?key=secret-in-query", json={"model": "m"},
                    headers={"Authorization": "Bearer sk-secret", "anthropic-beta": "b1", "x-api-key": "sk-2"})
        with client.stream("POST", "https://api.example.test/stream", json={}) as response:
            for _ in response.iter_bytes():
                pass
        client.get("https://elsewhere.test/")
    assert len(capture.exchanges) == 2
    first, streamed = capture.exchanges
    assert first.url == "https://api.example.test/v1" and first.request_json == {"model": "m"}
    assert first.request_headers == {"anthropic-beta": "b1", "content-type": "application/json"}
    assert first.json()["usage"] == {"input_tokens": 3}
    assert streamed.sse_events() == [{"type": "message_start"}]
    assert "sk-secret" not in json.dumps([vars(e) for e in capture.exchanges], default=str)


def test_the_fixture_server_serves_its_pages_on_loopback_only() -> None:
    with FixtureServer() as server:
        assert server.url.startswith("http://127.0.0.1:")
        page = httpx.get(f"{server.url}/article.html", timeout=5, trust_env=False)
        missing = httpx.get(f"{server.url}/nothing", timeout=5, trust_env=False)
    assert page.status_code == 200 and ARTICLE_CODEWORD in page.text and missing.status_code == 404


def test_log_tallies_count_known_lines_without_keeping_text() -> None:
    import logging

    from tests.e2e.live_eval.runner import LogTally

    tally = LogTally()
    logger = logging.getLogger("row_bot.live_eval_test")
    logger.addHandler(tally)
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    try:
        logger.debug("stable prefix: reused 3 of 4 items, +1 appended")
        logger.info("History hygiene: cleared earlier tool output in one step (batch, ~9000 tokens, jump 1)")
        logger.info("anthropic_thinking_binding: API dropped 2 thinking block(s) model=x drops=...")
        logger.info("unrelated")
    finally:
        logger.removeHandler(tally)
    assert tally.take() == {"prefix_stable": 1, "hygiene_jumps": 1, "thinking_dropped_logs": 1}
    assert tally.take() == {}


def test_ollama_analysis_flags_reloads_context_changes_and_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.e2e.live_eval import runner
    from tests.e2e.live_eval.capture import Exchange

    monkeypatch.setattr(runner, "_ollama_expiry_minutes", lambda *_args, **_kwargs: 29.5)
    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)

    def exchange(num_ctx: int, keep_alive: str, load_ns: int) -> Exchange:
        item = Exchange("POST", "http://127.0.0.1:11434/api/chat",
                        request_json={"model": "qwen3:8b", "options": {"num_ctx": num_ctx}, "keep_alive": keep_alive})
        item.body.extend(json.dumps({"done": True, "load_duration": load_ns, "prompt_eval_count": 10}).encode())
        return item

    capture = HttpCapture()
    capture.exchanges = [exchange(65536, "30m", 2_000_000_000), exchange(65536, "30m", 5_000_000)]
    healthy = runner.analyze_ollama(capture, "qwen3:8b")
    assert healthy["problems"] == [] and healthy["requests"][0]["load_ms"] == 2000.0
    capture.exchanges.append(exchange(8192, "5m", 3_000_000_000))
    problems = " ".join(runner.analyze_ollama(capture, "qwen3:8b")["problems"])
    assert "num_ctx differs" in problems and "keep_alive" in problems and "loaded the model again" in problems


def test_a_task_runs_through_the_agent_and_records_ledger_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    from tests.e2e.live_eval import runner
    from tests.e2e.live_eval.runner import BudgetGate, LogTally, Runtime, run_tasks

    # The runner imports row_bot.agent when it runs, so patch the module it will get now,
    # not the one this file bound at collection (an earlier test may have re-bound it).
    live_agent = importlib.import_module("row_bot.agent")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    calls: list[str] = []

    def fake_stream_agent(prompt, tool_names, config):
        calls.append(prompt)
        _record_usage(data_dir, monkeypatch, input_tokens=3_000, cache_read_tokens=2_400, output_tokens=12)
        yield ("tool_call", live_agent.ToolCallPayload("Calculator", raw_name="calculate", args={"expression": "17**3+289"}))
        yield ("tool_done", {"raw_name": "calculate", "content": "5202"})
        yield ("done", "5202")

    monkeypatch.setattr(live_agent, "stream_agent", fake_stream_agent)
    monkeypatch.setattr(Runtime, "_new_thread", lambda self, task: "le_thread")
    monkeypatch.setattr("row_bot.tools.registry.set_tool_config", lambda *args: None)
    caps = Caps(None, 1_000_000)
    runtime = Runtime("anthropic", "claude-test", tmp_path / "ws", caps)
    runtime.gate = BudgetGate(caps, {}, lambda: data_dir / "usage.db")
    runtime.tally = LogTally()
    records = run_tasks(runtime, [by_id()["calc_single"]], BudgetGuard(caps))
    assert [record.status for record in records] == ["pass"]
    assert records[0].totals.input_tokens == 3_000 and records[0].totals.cache_read_share == pytest.approx(0.8)
    assert records[0].tools_used == ["calculate"] and calls

    calls.clear()
    stopped = run_tasks(runtime, [by_id()["calc_single"], by_id()["chat_exact"]], BudgetGuard(Caps(None, 10)))
    assert [record.status for record in stopped] == ["budget_stopped", "budget_stopped"] and not calls
    # Signature (8) + IHDR chunk (25) + IDAT length and type (8); then IDAT's CRC (4) and IEND (12).
    assert runner.tiny_png().startswith(b"\x89PNG") and zlib.decompress(runner.tiny_png()[41:-16])
