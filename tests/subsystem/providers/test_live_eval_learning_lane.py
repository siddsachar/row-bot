"""The live eval's learning tier and its ChatGPT lane (L0c, D31), wired with fakes: no provider,
no network, no real credential store.

The ChatGPT lane's credentials come from the eval profile's own credential-store service,
reach the worker through a pipe, and only refreshed tokens go back; nothing secret lands in
the run folder or the report, the everyday profile is refused, and the lane has a token cap.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from row_bot.learning.packs import visible_tasks
from row_bot.learning.packs.verify import solve
from tests.e2e.live_eval import eval_profile, learning, orchestrate, providers as provider_registry
from tests.e2e.live_eval.budget import BudgetGuard, Caps
from tests.e2e.live_eval.report import Report, redact, register_secret, write_report
from tests.e2e.live_eval.tasks import Context, Outcome, TurnResult, by_id, select_tasks

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def _fake_token(kind: str) -> str:
    # Built at run time: a token-shaped literal in the source would trip secret scanning.
    return "-".join(["fake", kind, "x" * 24, kind[::-1] * 3])


ACCESS, REFRESH, ID_TOKEN, NEW_ACCESS, NEW_REFRESH = (
    _fake_token(kind) for kind in ("access", "refresh", "identity", "access2", "refresh2"))


def _profile(tmp_path: Path, *, signed_in: bool = True) -> Path:
    profile = tmp_path / "eval-profile"
    profile.mkdir()
    if signed_in:
        entry = {"auth_method": "oauth_device", "source": "oauth_device",
                 "oauth_bundle_ref": {"values": {"access_token": {"generation": "0" * 32, "chunks": 1}}}}
        (profile / "providers.json").write_text(json.dumps({"providers": {"codex": entry}}), encoding="utf-8")
    return profile


# ── The learning tier ──────────────────────────────────────────────────────


def test_the_learning_tier_runs_the_packs_and_the_replay_fixtures_on_every_provider() -> None:
    chosen = select_tasks("codex", {"ROW_BOT_LIVE_EVAL_TIER": "learning"})
    assert len(chosen) == 4 * 8 + len(learning.REPLAY_FIXTURES)
    assert {task.category for task in chosen} == {"learning:document_lookups", "learning:structured_transforms",
                                                  "learning:small_coding", "learning:tool_workflows",
                                                  "learning:replay"}
    every = select_tasks("ollama", {"ROW_BOT_LIVE_EVAL_TIER": "learning", learning.PER_PACK_ENV: "all"})
    assert len(every) == 64 + 6  # a local model runs the whole tier, not the local short set
    named = select_tasks("openai", {"ROW_BOT_LIVE_EVAL_TIER": "learning",
                                    "ROW_BOT_LIVE_EVAL_TASKS": "learn_tool_06,replay_close_issue"})
    assert [task.task_id for task in named] == ["learn_tool_06", "replay_close_issue"]
    assert named[0].tools == ("learning_calendar", "learning_mail")
    assert by_id()["learn_doc_01"].tools == ("filesystem",)
    with pytest.raises(ValueError):
        select_tasks("openai", {"ROW_BOT_LIVE_EVAL_TIER": "learning", "ROW_BOT_LIVE_EVAL_TASKS": "learn_hdoc_01"})
    with pytest.raises(ValueError):
        learning.per_pack({learning.PER_PACK_ENV: "17"})


def test_a_learning_task_is_checked_by_its_packs_verifier(tmp_path: Path) -> None:
    pack = {task.id: task for task in visible_tasks()}
    tasks = by_id()
    transform = pack["st_08"]
    (tmp_path / "paid_orders.json").write_text(transform.reference.files["paid_orders.json"], encoding="utf-8")
    done = [TurnResult(prompt="p", answer="done", status="done")]
    assert tasks["learn_st_08"].check(Outcome(turns=done, workspace=tmp_path), Context()).passed

    errand = pack["tool_02"]
    finished = Outcome(turns=done, workspace=tmp_path, collected={"fake_apps": solve(errand, errand.reference).apps})
    wrong = Outcome(turns=done, workspace=tmp_path, collected={"fake_apps": solve(errand, errand.decoy).apps})
    assert tasks["learn_tool_02"].check(finished, Context()).passed
    assert not tasks["learn_tool_02"].check(wrong, Context()).passed
    failed = Outcome(turns=[TurnResult(prompt="p", status="error", errors=["boom"])])
    assert "did not finish" in tasks["learn_doc_01"].check(failed, Context()).reason


def test_the_fake_apps_become_tools_the_agent_can_bind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.tools import registry

    monkeypatch.setattr(registry, "_tools", dict(registry._tools))
    monkeypatch.setattr(registry, "_enabled", dict(registry._enabled))
    learning.register_tools()
    task = by_id()["learn_tool_07"]
    task.seed(tmp_path, Context())
    [tool] = [item for item in registry.get_tool("learning_issues").as_langchain_tools() if item.name == "issues_update"]
    assert json.loads(tool.invoke({"issue_id": "12", "state": "closed", "add_labels": ["fixed"]}))["updated"]["state"] == "closed"
    assert learning.CURRENT.apps.state()["issues"]["issues"][2]["labels"] == ["bug", "fixed"]
    assert not registry.is_enabled("learning_issues")  # never part of anyone's default tool set


def test_a_replay_fixture_is_re_asked_with_the_runs_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.learning import evaluation
    from tests.fixtures.learning_eval import ScriptedClient

    def closes(_messages, _tools, _model):
        return AIMessage(content="", tool_calls=[{"name": "issues_update", "args": {"issue_id": 12, "state": "closed"},
                                                  "id": "c1", "type": "tool_call"}])

    client = ScriptedClient(closes)
    monkeypatch.setattr(evaluation, "LedgerModelClient", lambda: client)
    result = learning.run_replay("replay_close_issue", "model:codex:gpt-5.6-sol")
    assert result["status"] == "passed" and client.calls[0]["model_ref"] == "model:codex:gpt-5.6-sol"
    assert client.calls[0]["purpose"] == "learning_test"
    assert [tool["function"]["name"] for tool in client.calls[0]["tools"]][:2] == ["issues_list", "issues_get"]
    assert {case.decision_id for case in learning.replay_cases()} == set(learning.REPLAY_FIXTURES)


# ── The ChatGPT lane: the eval profile ─────────────────────────────────────


def test_the_lane_is_available_only_from_a_signed_in_eval_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex = provider_registry.target("codex")
    unset = provider_registry.availability(codex, {})
    assert not unset.available and eval_profile.PROFILE_ENV in unset.reason
    empty = _profile(tmp_path / "a", signed_in=False) if (tmp_path / "a").mkdir() is None else None
    no_sign_in = provider_registry.availability(codex, {eval_profile.PROFILE_ENV: str(empty),
                                                         eval_profile.SIGN_IN_ENV: "the sign-in script"})
    assert not no_sign_in.available
    assert no_sign_in.reason == "the eval profile has no ChatGPT sign-in: run the sign-in script"
    missing = provider_registry.availability(codex, {eval_profile.PROFILE_ENV: str(tmp_path / "nowhere")})
    assert not missing.available and "does not exist yet" in missing.reason
    assert missing.reason.startswith("the eval profile has no ChatGPT sign-in") and "run " in missing.reason
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    (home / ".row-bot").mkdir(parents=True)
    everyday = provider_registry.availability(codex, {eval_profile.PROFILE_ENV: str(home / ".row-bot")})
    assert not everyday.available and "refusing the everyday" in everyday.reason
    ready = provider_registry.availability(codex, {eval_profile.PROFILE_ENV: str(_profile(tmp_path))})
    assert ready.available and ready.model == "auto"
    assert eval_profile.choose_model(["gpt-5.5", "gpt-6.1-sol", "gpt-5.6-sol"]) == "gpt-6.1-sol"
    assert eval_profile.choose_model(["gpt-5.5", "gpt-5.6-sol"]) == "gpt-5.6-sol"


def test_the_plan_shows_the_lane_and_its_cap_without_any_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_subprocess(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("planning must not start a process")

    monkeypatch.setattr(orchestrate.subprocess, "run", no_subprocess)
    monkeypatch.setattr(eval_profile.subprocess, "run", no_subprocess)
    env = {eval_profile.PROFILE_ENV: str(_profile(tmp_path)), "ROW_BOT_LIVE_EVAL_PROVIDERS": "codex",
           "ROW_BOT_LIVE_EVAL_TIER": "learning"}
    plan = orchestrate.build_plan(env)
    [row] = plan["providers"]
    assert row["cap_tokens"] == 1_500_000 and row["fits_cap"] and row["model"].startswith("gpt-6.1-sol or")
    tight = orchestrate.build_plan({**env, eval_profile.MAX_TOKENS_ENV: "100000"})["providers"][0]
    assert tight["cap_tokens"] == 100_000 and not tight["fits_cap"]
    assert "lane cap 100,000 tokens: the run stops before passing it" in orchestrate.render_plan(
        orchestrate.build_plan({**env, eval_profile.MAX_TOKENS_ENV: "100000"}))
    local = orchestrate.build_plan({"ROW_BOT_LIVE_EVAL_OLLAMA_MODEL": "qwen3:8b", "ROW_BOT_LIVE_EVAL_PROVIDERS": "ollama",
                                    "ROW_BOT_LIVE_EVAL_TIER": "learning", "ROW_BOT_LIVE_EVAL_MAX_TOKENS": "1000"})
    assert local["providers"][0]["priced_by"] == "free (local)" and local["fits"] and local["total_tokens"] == 0


class FakeKeyring:
    """A credential store that remembers which service every call named."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.services: set[str] = set()

    def get_password(self, service: str, account: str) -> str | None:
        self.services.add(service)
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.services.add(service)
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        self.services.add(service)
        if (service, account) not in self.values:
            raise KeyError("not found")
        del self.values[(service, account)]


def _bound_to(folder: Path, reload_for_data_dir: Any, monkeypatch: pytest.MonkeyPatch, keyring: FakeKeyring) -> Any:
    secret_store, _config = reload_for_data_dir(folder, "row_bot.secret_store", "row_bot.providers.config")
    monkeypatch.setattr(secret_store, "_backend_override", keyring)
    from row_bot.providers import codex

    monkeypatch.setattr(codex, "save_codex_oauth_tokens", codex.save_codex_oauth_tokens)
    return codex


def test_the_bridge_reads_and_writes_back_only_under_the_eval_profiles_own_service(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload_for_data_dir: Any) -> None:
    from row_bot import secret_store

    profile = tmp_path / "eval-profile"
    profile.mkdir()
    keyring = FakeKeyring()
    codex = _bound_to(profile, reload_for_data_dir, monkeypatch, keyring)
    codex.save_codex_oauth_tokens(codex.CodexTokenSet(access_token=ACCESS, refresh_token=REFRESH, id_token=ID_TOKEN,
                                                      account_id="acct-1", expires_at="2030-01-01T00:00:00+00:00"))
    assert eval_profile.signed_in(profile)
    keyring.services.clear()

    read = eval_profile.bridge_read()
    assert read["signed_in"] and (read["access_token"], read["refresh_token"], read["account"]) == (ACCESS, REFRESH, "acct-1")
    assert keyring.services == {secret_store.service_name_for(profile)}  # the eval profile's own service only

    refreshed = {**read, "access_token": NEW_ACCESS, "refresh_token": NEW_REFRESH}
    assert eval_profile.bridge_write(refreshed) == {"written": True}
    again = eval_profile.bridge_read()
    assert (again["access_token"], again["refresh_token"], again["id_token"]) == (NEW_ACCESS, NEW_REFRESH, ID_TOKEN)
    # Written against the revision it read: a profile signed in again meanwhile is left as it is.
    assert eval_profile.bridge_write({**refreshed, "access_token": ACCESS}) == {
        "written": False, "reason": "the eval profile changed during the run"}
    assert keyring.services == {secret_store.service_name_for(profile)}
    text = (profile / "providers.json").read_text(encoding="utf-8")
    assert not any(token in text for token in (ACCESS, REFRESH, NEW_ACCESS, NEW_REFRESH, ID_TOKEN))


def test_the_worker_keeps_the_sign_in_in_memory_and_reports_refreshed_tokens(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload_for_data_dir: Any) -> None:
    worker_folder = tmp_path / "run" / "data" / "learning-codex"
    worker_folder.mkdir(parents=True)
    keyring = FakeKeyring()
    codex = _bound_to(worker_folder, reload_for_data_dir, monkeypatch, keyring)
    from row_bot import secret_store

    monkeypatch.setattr(secret_store, "_set_backend_for_tests", lambda backend: None)  # keep the recording fake
    reported: list[eval_profile.CodexBundle] = []
    eval_profile.seed_worker({"access_token": ACCESS, "refresh_token": REFRESH, "id_token": ID_TOKEN,
                              "account": "acct-1", "expires_at": "2030-01-01T00:00:00+00:00"}, emit=reported.append)
    credentials = codex.codex_runtime_credentials(refresh_if_needed=False)
    assert (credentials.access_token, credentials.account_id) == (ACCESS, "acct-1") and reported == []

    # A refresh during the run (the transport saves what it got back; the refresh token may be left out).
    codex.save_codex_oauth_tokens(codex.CodexTokenSet(access_token=NEW_ACCESS, expires_at="2030-02-01T00:00:00+00:00"))
    [refreshed] = reported
    assert (refreshed.access_token, refreshed.refresh_token, refreshed.account) == (NEW_ACCESS, REFRESH, "acct-1")
    assert keyring.services == {secret_store.service_name_for(worker_folder)}
    assert redact(f"error with {NEW_ACCESS} and {REFRESH}") == "error with [redacted] and [redacted]"
    eval_profile.scrub_worker_folder(worker_folder)
    assert "codex" not in json.loads((worker_folder / "providers.json").read_text(encoding="utf-8")).get("providers", {})


def test_the_lane_hands_the_sign_in_to_the_worker_and_writes_back_only_refreshed_tokens(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _profile(tmp_path)
    monkeypatch.setenv(eval_profile.PROFILE_ENV, str(profile))
    monkeypatch.setenv(eval_profile.MAX_TOKENS_ENV, "400000")
    bridge_calls: list[tuple[str, dict[str, Any] | None]] = []

    def bridge(folder: Path, command: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        assert folder == profile
        bridge_calls.append((command, payload))
        if command == "read":
            return {"signed_in": True, "access_token": ACCESS, "refresh_token": REFRESH, "id_token": ID_TOKEN,
                    "account": "acct-1", "expires_at": "2030-01-01", "plan_type": "plus", "revision": "rev-7"}
        return {"written": True}

    seen: dict[str, Any] = {}

    def worker(argv: list[str], **kwargs: Any) -> Any:
        seen.update(argv=argv, input=kwargs.get("input"), env=kwargs["env"])
        out = Path(argv[argv.index("--out") + 1])
        data_dir = Path(kwargs["env"]["ROW_BOT_DATA_DIR"])
        (data_dir / "providers.json").write_text(json.dumps({"providers": {"codex": {"fingerprint": "****"}}}),
                                                 encoding="utf-8")
        out.write_text(json.dumps({"provider": "codex", "model": "gpt-5.6-sol", "status": "error", "tasks": [],
                                   "spent": {"tokens": 1_000}, "reason": "worker failed"}), encoding="utf-8")
        frame = eval_profile.FRAME + json.dumps({"access_token": NEW_ACCESS, "refresh_token": NEW_REFRESH,
                                                 "id_token": ID_TOKEN, "account": "acct-1"})
        return types.SimpleNamespace(returncode=1, stdout=f"noise\n{frame}\n",
                                     stderr=f"Traceback: 401 for token {ACCESS}\n")

    monkeypatch.setattr(orchestrate.subprocess, "run", worker)
    run_dir = tmp_path / "run"
    state = provider_registry.availability(provider_registry.target("codex"))
    result = orchestrate.run_provider(state, ["learn_doc_01"], run_dir, BudgetGuard(Caps(None, 2_000_000)),
                                      label="learning", bridge=bridge)

    assert "--credentials" in seen["argv"] and seen["argv"][seen["argv"].index("--credentials") + 1] == "stdin"
    handed = json.loads(seen["input"])
    assert handed["access_token"] == ACCESS and "revision" not in handed
    assert seen["argv"][seen["argv"].index("--max-tokens") + 1] == "400000"  # the lane's cap
    assert seen["env"]["ROW_BOT_TEST_MODE"] == "true" and Path(seen["env"]["ROW_BOT_DATA_DIR"]).is_relative_to(run_dir)
    assert result["credentials"] == "written"
    [_, (command, payload)] = bridge_calls
    assert command == "write" and payload["access_token"] == NEW_ACCESS and payload["revision"] == "rev-7"
    assert ACCESS not in result["stderr_tail"] and "[redacted]" in result["stderr_tail"]
    write_report(Report("run", providers=[{"provider": "codex", **{k: v for k, v in result.items() if k != "tasks"}}]),
                 run_dir)
    assert "(sign-in tokens: written)" in (run_dir / "report.md").read_text(encoding="utf-8")
    for path in run_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert not any(token in text for token in (ACCESS, REFRESH, NEW_ACCESS, NEW_REFRESH, ID_TOKEN)), path

    # Nothing refreshed: nothing is written back.
    bridge_calls.clear()

    def quiet_worker(argv: list[str], **kwargs: Any) -> Any:
        Path(argv[argv.index("--out") + 1]).write_text(json.dumps({"status": "ran", "tasks": [], "spent": {}}),
                                                       encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(orchestrate.subprocess, "run", quiet_worker)
    again = orchestrate.run_provider(state, ["learn_doc_01"], tmp_path / "run2", BudgetGuard(Caps(None, 10**7)),
                                     bridge=bridge)
    assert again["credentials"] == "unchanged" and [command for command, _ in bridge_calls] == ["read"]


def test_the_lane_never_starts_without_a_sign_in_or_on_the_everyday_profile(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_process(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("no worker or bridge may start")

    monkeypatch.setattr(orchestrate.subprocess, "run", no_process)
    state = provider_registry.availability(provider_registry.target("codex"), {})
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(eval_profile.PROFILE_ENV, str(empty))
    monkeypatch.setenv(eval_profile.SIGN_IN_ENV, "sign-in-eval-profile.ps1")
    skipped = orchestrate.run_provider(state, ["learn_doc_01"], tmp_path / "run", BudgetGuard(Caps()), bridge=no_process)
    assert skipped["status"] == "skipped" and skipped["reason"].endswith("run sign-in-eval-profile.ps1")
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    (home / ".row-bot").mkdir(parents=True)
    monkeypatch.setenv(eval_profile.PROFILE_ENV, str(home / ".row-bot"))
    refused = orchestrate.run_provider(state, ["learn_doc_01"], tmp_path / "run", BudgetGuard(Caps()), bridge=no_process)
    assert refused["status"] == "skipped" and "everyday" in refused["reason"]
    with pytest.raises(eval_profile.EvalProfileError):
        eval_profile.main(["read", "--profile", str(home / ".row-bot")])


def test_in_memory_credentials_are_redacted_like_environment_keys() -> None:
    secret = _fake_token("memory")
    assert redact(f"echoed {secret}") == f"echoed {secret}"
    register_secret(secret)
    assert redact(f"echoed {secret}") == "echoed [redacted]"


def test_the_ollama_lane_takes_each_models_capabilities_from_ollama_itself(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.providers import model_catalog, model_catalog_cache
    from row_bot.providers.capability_resolution import cached_ollama_capability_snapshot
    from row_bot.providers.ollama import ollama_catalog_rows
    from tests.e2e.live_eval.runner import refresh_local_catalog

    monkeypatch.setattr(model_catalog_cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    model_catalog_cache.write_model_catalog_cache(model_catalog_cache.empty_catalog_cache())
    personal = "row-bot-personal-v3:latest"  # not in Row-Bot's family list
    assert not cached_ollama_capability_snapshot(personal)
    monkeypatch.setattr(model_catalog, "load_ollama_catalog_rows", lambda: ollama_catalog_rows(
        [personal], [], metadata_by_model={personal: {"capabilities": ["completion", "tools"]}}))
    assert refresh_local_catalog() == "local catalog: 1 Ollama models with their own capability answers"
    assert cached_ollama_capability_snapshot(personal)["tool_calling"] is True


def test_a_local_model_is_free_so_the_caps_never_hold_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.prompt_cache import ModelUsage
    from row_bot.providers.usage_ledger import UsageRecord, record_model_usage
    from tests.e2e.live_eval.runner import BudgetGate

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    for provider in ("ollama", "openai"):
        assert record_model_usage(UsageRecord(purpose="main", provider=provider, model="m",
                                              usage=ModelUsage(input_tokens=50_000, output_tokens=100)))
    gate = BudgetGate(Caps(None, 60_000), {}, lambda: tmp_path / "usage.db")
    assert gate.spent().tokens == 50_100
    gate.before_call("ollama", "qwen3:8b", [[AIMessage(content="x" * 400_000)]], {})  # never refused
    with pytest.raises(Exception, match="live eval budget"):
        gate.before_call("openai", "gpt", [[AIMessage(content="x" * 400_000)]], {})
    assert sys.modules["tests.e2e.live_eval.runner"].FREE_PROVIDERS == frozenset({"ollama"})
