"""The company's policy in the app (C3a), against the fake company server with an injected clock: fetched with
its ETag (304 when unchanged), settings this app doesn't know left out, cached across a restart, the offline lease
running out into lockdown (also at once with ``max_days`` 0 or a disconnect) and recovering, the refresh limits,
the model settings hiding the right providers and refusing a chat that names a hidden one, the person's keys left
alone, and with no company joined: no policy, no change and no network."""

from __future__ import annotations

import hashlib
import json
import logging
from types import SimpleNamespace

import pytest

from row_bot import secret_store
from row_bot.application import client_company
from row_bot.application import live_updates as live_module
from row_bot.application.live_updates import LiveUpdates
from row_bot.company import policy, provider
from row_bot.policy import canonical_json, lockdown_policy
from row_bot.providers import backup, client_status, config as provider_config, model_catalog_cache
from row_bot.providers.model_catalog import build_saved_model_catalog_rows
from row_bot.providers.runtime import create_chat_model

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

DAY = 24 * 60 * 60
EFFECTIVE = "/api/policy/effective"
LMSTUDIO, TEAM_BOX = "custom_openai_lmstudio", "custom_openai_team-box"
# One model of each kind this computer could use: (provider, model, what it counts as).
MODELS = [
    ("openai", "gpt-5", "personal"),
    (TEAM_BOX, "llama-remote", "personal"),
    ("codex", "gpt-5.6-sol", "consumer"),
    ("ollama", "qwen3:8b", "local"),
    (LMSTUDIO, "qwen3-local", "local"),
]
PERSONAL_ROWS = {"openai", TEAM_BOX, "codex", "ollama", LMSTUDIO}


@pytest.fixture
def clock(monkeypatch):
    """The policy's clock (seconds since the epoch), moved by the test."""
    now = [1_800_000_000.0]
    monkeypatch.setattr(policy, "now", lambda: now[0])
    return now


@pytest.fixture
def lists(tmp_path, monkeypatch, company_server):
    """This computer's own models (saved, as after a catalog read), its two custom endpoints (one on loopback, one
    elsewhere), and fresh live topics."""
    live = LiveUpdates()
    monkeypatch.setattr(live_module, "live_updates", live)
    monkeypatch.setattr(model_catalog_cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    cloud = {f"model:{provider_id}:{model_id}": {"provider": provider_id, "label": model_id}
             for provider_id, model_id, _ in MODELS if provider_id != "ollama"}
    model_catalog_cache.write_model_catalog_cache(model_catalog_cache.CatalogCacheSnapshot(
        1, 1000.0, cloud, [{"model_id": "qwen3:8b", "installed": True}], {}, (), "test"))
    provider_config.save_provider_config({"custom_endpoints": [
        {"id": "lmstudio", "name": "LM Studio", "base_url": "http://127.0.0.1:1234/v1", "enabled": True},
        {"id": "team-box", "name": "Team box", "base_url": "https://llm.example.test/v1", "enabled": True},
    ]})
    client_status.forget_saved_reads()
    return live


def _providers() -> set[str]:
    """The providers the app's model lists show (the pickers and Settings › Models are built from these rows)."""
    client_status.forget_saved_reads()
    return {row.provider_id for row in client_status.list_cached_models(limit=100).items}


def _fetches(server) -> int:
    return sum(1 for request in server.requests if request.url.path == EFFECTIVE)


def _etag(body: dict) -> str:
    return '"' + hashlib.sha256(canonical_json(body)).hexdigest() + '"'


def _set(server, **models) -> None:
    server.policy["models"].update(models)


def test_the_policy_is_fetched_with_its_etag_and_an_unchanged_one_is_confirmed_by_a_304(company_server, clock):
    company_server.join_here()
    (first,) = [request for request in company_server.requests if request.url.path == EFFECTIVE]
    assert "if-none-match" not in first.headers and first.headers["authorization"].startswith("Bearer rboa_")
    assert "cookie" not in first.headers
    assert policy.state().state == "current" and policy.current().offline.max_days == 7

    clock[0] += 600
    status = policy.refresh()

    second = [request for request in company_server.requests if request.url.path == EFFECTIVE][-1]
    assert second.headers["if-none-match"] == _etag(company_server.policy)
    assert company_server.policy_answers == [200, 304]
    assert (status.state, status.since) == ("current", clock[0])  # Confirmed again.

    company_server.policy["offline"]["max_days"] = 3
    assert policy.refresh().policy.offline.max_days == 3
    assert company_server.policy_answers == [200, 304, 200]


def test_settings_this_app_doesnt_know_are_left_out_and_named_once(company_server, caplog):
    caplog.set_level(logging.INFO, logger="row_bot.company.policy")
    company_server.policy["models"]["future_switch"] = True
    company_server.policy["telemetry"] = {"anything": 1}

    company_server.join_here()

    assert policy.state().state == "current"
    told = [record.getMessage() for record in caplog.records if "doesn't know" in record.getMessage()]
    assert len(told) == 1 and "models.future_switch" in told[0] and "telemetry" in told[0]
    company_server.policy["offline"]["max_days"] = 3  # A new answer with the same unknown settings:
    assert policy.refresh().policy.offline.max_days == 3
    assert len([record for record in caplog.records if "doesn't know" in record.getMessage()]) == 1  # not again.
    # A known setting with a wrong value is still refused: the cached policy is kept.
    company_server.policy["offline"]["max_days"] = "soon"
    status = policy.refresh()
    assert (status.state, status.reason, status.policy.offline.max_days) == ("cached", "unreachable", 3)


def test_the_cached_policy_holds_across_a_restart_and_a_file_that_doesnt_check_out_is_not_used(
        company_server, clock, monkeypatch):
    _set(company_server, local_models=False)
    company_server.join_here()
    kept = policy.current()
    # A restart: nothing in memory, and the server out of reach.
    monkeypatch.setattr(policy, "_memo", None)
    monkeypatch.setattr(policy, "_attempted_at", None)
    company_server.offline = True

    assert policy.current() == kept and kept.models.local_models is False
    assert policy.state().state == "current"

    data = json.loads(policy.cache_path().read_text(encoding="utf-8"))
    assert set(data) >= {"origin", "device_id", "etag", "confirmed_at", "policy"}
    data["confirmed_at"] = clock[0] + 30 * DAY  # Confirmed in the future: not to be believed.
    policy.cache_path().write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(policy, "_memo", None)

    status = policy.state()
    assert (status.state, status.reason, status.policy) == ("locked_down", "not_confirmed", lockdown_policy())


def test_the_lease_runs_out_into_lockdown_which_ends_at_the_next_fetch(company_server, clock):
    company_server.policy["offline"]["max_days"] = 2
    company_server.join_here()
    confirmed = clock[0]

    company_server.offline = True
    clock[0] += DAY
    status = policy.refresh()
    assert (status.state, status.reason) == ("cached", "unreachable")  # Within the lease: the cached policy holds.
    assert status.policy.offline.max_days == 2

    clock[0] = confirmed + 2 * DAY + 1
    status = policy.state()
    assert (status.state, status.reason, status.since) == ("locked_down", "lease_expired", confirmed + 2 * DAY)
    assert policy.current() == lockdown_policy()
    assert policy.lockdowns() == [{"started_at": confirmed + 2 * DAY, "ended_at": None, "reason": "lease_expired",
                                   "offline_days": 2}]
    view = client_company.status()
    assert (view["policy_state"], view["policy_reason"]) == ("locked_down", "lease_expired")
    assert {lock["setting"] for lock in view["locked"]} == {"models.personal_api_keys", "models.consumer_sign_ins"}

    company_server.offline = False
    clock[0] += 60
    assert policy.refresh().state == "current"
    assert policy.current().offline.max_days == 2
    assert policy.lockdowns() == [{"started_at": confirmed + 2 * DAY, "ended_at": clock[0],
                                   "reason": "lease_expired", "offline_days": 2}]


def test_with_no_offline_days_a_failed_refresh_locks_down_at_once(company_server, clock):
    company_server.policy["offline"]["max_days"] = 0
    company_server.join_here()
    clock[0] += 30 * DAY
    assert policy.state().state == "current"  # Nothing failed yet.

    company_server.offline = True
    status = policy.refresh()

    assert (status.state, status.reason, status.since) == ("locked_down", "lease_expired", clock[0])
    company_server.offline = False
    assert policy.refresh().state == "current"


def test_a_disconnected_row_bot_locks_down_at_once_and_asks_nothing_more(company_server, clock):
    record = company_server.join_here()
    company_server.revoke(record.device_id)

    status = policy.refresh()

    assert (status.state, status.reason, status.policy) == ("locked_down", "disconnected", lockdown_policy())
    assert policy.lockdowns()[-1]["reason"] == "disconnected"
    asked = len(company_server.requests)
    assert policy.refresh().state == "locked_down"
    assert len(company_server.requests) == asked


def test_a_model_list_or_a_chat_refreshes_at_most_every_fifteen_minutes_and_settings_at_once(
        company_server, lists, monkeypatch):
    moment = [5000.0]
    monkeypatch.setattr(policy, "monotonic", lambda: moment[0])
    company_server.join_here()
    provider.refresh()
    assert _fetches(company_server) == 1

    for step in (0, policy.REFRESH_SECONDS - 1):
        moment[0] = 5000.0 + step
        _providers()
        policy.settle()
        assert _fetches(company_server) == 1
    moment[0] = 5000.0 + policy.REFRESH_SECONDS
    _providers()
    policy.settle()
    assert _fetches(company_server) == 2
    _providers()
    create_chat_model("qwen3-32b", provider_id="company")  # A chat model made is use too, within the limit.
    policy.settle()
    assert _fetches(company_server) == 2

    moment[0] += policy.REFRESH_SECONDS
    create_chat_model("qwen3-32b", provider_id="company")
    policy.settle()
    assert _fetches(company_server) == 3
    client_company.check()  # Settings › Company opened: at once, whatever the limit.
    assert _fetches(company_server) == 4


CASES = [
    # personal API keys, consumer sign-ins, local models -> the providers whose models stay listed
    ((False, False, True), {"ollama", LMSTUDIO}),  # The defaults.
    ((True, False, False), {"openai", TEAM_BOX}),
    ((False, True, False), {"codex"}),
    ((True, True, True), PERSONAL_ROWS),
]


@pytest.mark.parametrize(("switches", "listed"), CASES)
def test_each_model_setting_hides_the_right_providers(company_server, lists, switches, listed):
    company_server.join_here()
    provider.refresh()
    personal, consumer, local = switches
    _set(company_server, personal_api_keys=personal, consumer_sign_ins=consumer, local_models=local)

    policy.refresh()

    assert _providers() == listed | {"company"}
    for provider_id, model_id, kind in MODELS:
        assert policy.provider_kind(provider_id) == kind
        assert policy.allows_provider(provider_id) is (provider_id in listed)
        assert (policy.turned_off(provider_id, model_id) is None) is (provider_id in listed)


@pytest.mark.parametrize(("provider_id", "model_id", "kind"), MODELS)
def test_a_chat_that_names_a_hidden_model_fails_in_plain_words_with_no_backup(
        company_server, lists, provider_id, model_id, kind):
    from row_bot.agent import _friendly_api_error
    from row_bot.application.turn_failure import failure_reason
    from row_bot.application.workspace_setup import model_status

    company_server.join_here()
    _set(company_server, personal_api_keys=False, consumer_sign_ins=False, local_models=False)
    policy.refresh()

    with pytest.raises(policy.ModelTurnedOff) as refused:
        create_chat_model(model_id, provider_id=provider_id)

    words = policy.REFUSED[kind]
    assert str(refused.value) == words
    assert failure_reason(_friendly_api_error(str(refused.value))) == words  # What the chat shows.
    assert backup.reason(refused.value) is None  # No other model answers in its place.
    # The composer's pill never shows it as ready.
    pill = model_status(SimpleNamespace(readiness_factory=None), {
        "model_selection": {"provider_id": provider_id, "model_ref": f"model:{provider_id}:{model_id}"}})
    assert (pill["state"], pill["reason"], pill["fix"]) == ("unavailable", policy.TURNED_OFF[kind], "choose")


def test_company_models_outside_the_allowed_list_are_hidden_and_refused(company_server, lists):
    company_server.join_here()
    provider.refresh()  # The catalog was read before the policy narrowed it.
    company_server.policy["models"]["allowed"] = ["claude-team"]

    policy.refresh()

    assert {row.model_id for row in build_saved_model_catalog_rows(cloud_cache={}, ollama_rows=[],
                                                                    provider_config={})} == {"claude-team"}
    with pytest.raises(policy.ModelTurnedOff, match="doesn't let you use this model"):
        create_chat_model("qwen3-32b", provider_id="company")
    assert policy.turned_off("company", "claude-team") is None


def test_locked_down_only_models_on_this_computer_are_listed_and_the_companys_say_why(
        company_server, lists, clock):
    company_server.policy["offline"]["max_days"] = 1
    _set(company_server, personal_api_keys=True, consumer_sign_ins=True)
    company_server.join_here()
    provider.refresh()
    assert _providers() == PERSONAL_ROWS | {"company"}

    company_server.offline = True
    clock[0] += 2 * DAY

    assert _providers() == {"ollama", LMSTUDIO, "company"}
    company = [row for row in build_saved_model_catalog_rows(cloud_cache={}, ollama_rows=[], provider_config={})
               if row.provider_id == "company"]
    assert {row.model_id for row in company} == {"qwen3-32b", "claude-team"}
    assert {(row.runtime_ready, row.status_reason) for row in company} == {(False, provider.LOCKED_DOWN)}
    with pytest.raises(policy.ModelTurnedOff) as refused:
        create_chat_model("qwen3-32b", provider_id="company")
    assert str(refused.value) == policy.REFUSED["locked_down"]

    company_server.offline = False
    policy.refresh()
    assert _providers() == PERSONAL_ROWS | {"company"}


def test_the_persons_own_keys_stay_in_the_keychain_whatever_the_policy(company_server, lists, clock):
    secret_store.set_secret("openai_api_key", "sk-personal-fixture", namespace="api_keys")
    settings_before = provider_config.CONFIG_PATH.read_bytes()
    company_server.join_here()
    assert "openai" not in _providers()  # Hidden by the default policy...

    company_server.offline = True
    clock[0] += 30 * DAY
    assert policy.state().state == "locked_down"
    company_server.offline = False
    _set(company_server, personal_api_keys=True)
    policy.refresh()
    assert "openai" in _providers()  # ...and back, with the same key.
    client_company.leave()

    assert secret_store.get_secret("openai_api_key", namespace="api_keys") == "sk-personal-fixture"
    assert provider_config.CONFIG_PATH.read_bytes() == settings_before
    assert not policy.cache_path().exists()  # The company's policy left with it.


def test_settings_shows_the_rules_in_plain_words_and_what_the_company_restricts(company_server):
    company_server.join_here()

    view = client_company.status()

    assert (view["policy_state"], view["policy_reason"]) == ("current", None) and view["policy_since"]
    assert [lock["words"] for lock in view["locked"]] == [
        "Your company turned off personal API keys", "Your company turned off personal subscriptions"]
    rules = {rule["setting"]: rule for rule in view["rules"]}
    assert rules["offline.max_days"]["value"] == "7 days" and rules["offline.max_days"]["title"] == "Offline days"
    assert rules["approvals.max_level"]["value"] == "Auto"
    assert rules["models.allowed"]["value"] == "All" and rules["models.local_models"]["value"] == "On"


def test_a_default_model_the_policy_took_away_names_the_companys_suggestion_and_chooses_nothing(
        company_server, lists):
    company_server.join_here()
    provider.refresh()

    assert policy.default_model_words("openai", "gpt-5", "GPT-5") == (
        "Your company turned off personal API keys, so GPT-5 can't be used. Choose another model, such as "
        "Qwen3 32B, which your company suggests.")
    assert policy.default_model_words("ollama", "qwen3:8b", "Qwen3 8B") is None


def test_not_joined_there_is_no_policy_no_change_and_no_network(company_server, lists):
    assert policy.current() is None and policy.state().state == "not_joined"
    assert _providers() == PERSONAL_ROWS
    assert policy.refresh().state == "not_joined"
    policy.refresh_soon()
    policy.settle()
    for provider_id, model_id, _ in MODELS:
        assert policy.allows_provider(provider_id) and policy.turned_off(provider_id, model_id) is None
    view = client_company.status()
    assert (view["policy_state"], view["locked"], view["rules"]) == ("not_joined", [], [])
    assert company_server.requests == [] and not policy.cache_path().exists()
