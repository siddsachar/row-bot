"""Company models (C2): a joined Row-Bot lists the company's chat models under its name and calls them through
the company's gateway with the device's bearer only; a 401 refreshes once; the gateway's refusals reach the person
in plain words; a disconnected Row-Bot fails clearly with no fallback; with no company joined there is no company
provider and nothing is asked. The company server and its gateway are a fake (``httpx.MockTransport``)."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import HumanMessage

from row_bot.application import client_company
from row_bot.application import live_updates as live_module
from row_bot.application.live_updates import LiveUpdates
from row_bot.company import gateway, provider, store
from row_bot.providers import backup, client_status, config as provider_config, model_catalog_cache
from row_bot.providers.runtime import create_chat_model
from tests.fixtures.company_server import COMPANY, GATEWAY_ANTHROPIC, GATEWAY_OPENAI, REPLY

pytestmark = [pytest.mark.subsystem]

QWEN, CLAUDE = "model:company:qwen3-32b", "model:company:claude-team"
# Personal keys this computer has: none of them may ever reach the company's gateway.
PERSONAL = {"OPENAI_API_KEY": "sk-personal-openai-fixture", "ANTHROPIC_API_KEY": "sk-ant-personal-fixture",
            "ANTHROPIC_AUTH_TOKEN": "personal-auth-token-fixture"}


@pytest.fixture
def lists(tmp_path, monkeypatch, company_server):
    """Empty saved catalogs and provider settings in the scratch folder, and fresh live topics."""
    live = LiveUpdates()
    monkeypatch.setattr(live_module, "live_updates", live)
    monkeypatch.setattr(model_catalog_cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    for name, value in PERSONAL.items():
        monkeypatch.setenv(name, value)
    client_status.forget_saved_reads()
    return live


def _company_rows(**query) -> dict:
    client_status.forget_saved_reads()
    page = client_status.list_cached_models(provider_id="company", readiness=True, **query)
    return {row.selection_ref: row for row in page.items}


def _paths(server) -> list[str]:
    return [request.url.path for request in server.requests]


def _sent_no_personal_key(server) -> None:
    for request in server.gateway:
        assert "x-api-key" not in request.headers and "cookie" not in request.headers
        assert request.headers["authorization"].startswith("Bearer rboa_")
        sent = json.dumps(dict(request.headers)) + request.content.decode("utf-8", "replace")
        assert not any(value in sent for value in PERSONAL.values())


def test_a_joined_row_bot_lists_the_companys_chat_models_under_its_name(company_server, lists):
    company_server.join_here()
    provider.refresh()

    rows = _company_rows()

    assert set(rows) == {QWEN, CLAUDE}  # Provider-qualified; the embedding model isn't offered.
    assert {row.provider_display_name for row in rows.values()} == {COMPANY}
    assert rows[QWEN].display_name == "Qwen3 32B" and rows[QWEN].context_window == 131072
    assert all(row.runtime_state == "ready" for row in rows.values())
    picker = {option["selection_ref"]: option for option in client_status.picker_choices("chat")}
    assert picker[QWEN]["label"] == f"Qwen3 32B - {COMPANY}" and picker[QWEN]["available"] is True
    assert picker[CLAUDE]["label"] == f"Claude for the team - {COMPANY}"
    # The catalog was asked for once, with the device's bearer.
    (asked,) = [request for request in company_server.requests if request.url.path == "/api/models"]
    assert asked.headers["authorization"].startswith("Bearer rboa_") and "cookie" not in asked.headers


def test_a_model_list_read_later_asks_for_the_catalog_again_in_the_background_and_nothing_polls(
        company_server, lists, monkeypatch):
    company_server.join_here()
    clock = [1000.0]
    monkeypatch.setattr(provider, "monotonic", lambda: clock[0])
    provider.refresh()
    _company_rows()
    provider.settle()
    assert _paths(company_server).count("/api/models") == 1  # Fresh: a list read asks nothing.

    company_server.catalog.append({"id": "llama-4", "display_name": "Llama 4", "kind": "chat", "api": "openai",
                                   "context_window": 65536, "default": False})
    seen = lists.frame()["topics"]["models"]
    clock[0] += provider.FRESH_SECONDS + 1
    _company_rows()  # The list answers at once with what it has, and asks again in the background.
    provider.settle()

    assert _paths(company_server).count("/api/models") == 2  # The catalog was read once more.
    assert lists.frame()["topics"]["models"] > seen  # Open lists read again,
    assert "model:company:llama-4" in _company_rows()  # and have the new model.


@pytest.mark.parametrize(("ref", "path"), [(QWEN, GATEWAY_OPENAI), (CLAUDE, GATEWAY_ANTHROPIC)])
def test_a_chat_goes_through_the_gateway_with_the_devices_bearer_and_no_personal_key(
        company_server, lists, ref, path):
    company_server.join_here()
    provider.refresh()
    model = create_chat_model(ref)

    answer = model.invoke([HumanMessage("Hello?")])
    streamed = "".join(str(chunk.content) if isinstance(chunk.content, str) else
                       "".join(part.get("text", "") for part in chunk.content if isinstance(part, dict))
                       for chunk in model.stream([HumanMessage("Hello again?")]))

    text = answer.content if isinstance(answer.content, str) else "".join(
        part.get("text", "") for part in answer.content if isinstance(part, dict))
    assert text == REPLY and streamed == REPLY
    assert [request.url.path for request in company_server.gateway] == [path, path]
    assert [json.loads(request.content).get("stream") in (True,) for request in company_server.gateway] == [
        False, True]
    assert {json.loads(request.content)["model"] for request in company_server.gateway} == {ref.split(":", 2)[2]}
    assert all(request.url.host == "rowbot.example.test" for request in company_server.gateway)
    _sent_no_personal_key(company_server)


def test_an_access_token_the_gateway_refuses_is_refreshed_once_and_the_call_sent_again(company_server, lists):
    company_server.join_here()
    provider.refresh()
    model = create_chat_model(QWEN)
    company_server.end_access()

    assert model.invoke([HumanMessage("Hello?")]).content == REPLY

    first, second = company_server.gateway
    assert first.headers["authorization"] != second.headers["authorization"]
    assert company_server.refreshes == 1
    assert second.headers["authorization"] == f"Bearer {store.read_credential().access_token}"
    assert store.read_record().disconnected is False


REFUSALS = [
    (403, "model_not_allowed", gateway.WORDS["model_not_allowed"]),
    (429, "budget_exceeded", "The monthly budget your company set for its models is used up. Ask your IT team, "
                             "or choose another model."),
    (429, "too_many_calls", gateway.WORDS["too_many_calls"]),
    (503, "endpoint_disabled", "Your company has switched this model off for now. Choose another model, or try "
                               "again later."),
    (504, "endpoint_timeout", "Your company's model didn't answer in time. Try again."),
    (502, "endpoint_unreachable", "Your company's model isn't answering right now. Try again later, or choose "
                                  "another model."),
    (400, "invalid_request", "Your company's server turned this request down: The request needs a model."),
]


@pytest.mark.parametrize("ref", [QWEN, CLAUDE])
@pytest.mark.parametrize(("status", "code", "words"), REFUSALS)
def test_each_gateway_refusal_reaches_the_person_in_plain_words_and_is_not_retried(
        company_server, lists, ref, status, code, words):
    from row_bot.agent import _friendly_api_error
    from row_bot.application.turn_failure import failure_reason

    company_server.join_here()
    provider.refresh()
    model = create_chat_model(ref)
    company_server.refusal = (status, code, "The request needs a model.")

    with pytest.raises(gateway.CompanyModelError) as refused:
        model.invoke([HumanMessage("Hello?")])

    assert str(refused.value) == words
    assert len(company_server.gateway) == 1  # Asked once: no SDK retry of a refusal.
    # The chat shows these words as the turn's reason, and no backup model answers in their place.
    assert failure_reason(_friendly_api_error(str(refused.value))) == words
    assert backup.reason(refused.value) is None


def test_a_disconnected_row_bot_fails_clearly_and_keeps_its_models_listed_unavailable(company_server, lists):
    record = company_server.join_here()
    provider.refresh()
    model = create_chat_model(QWEN)
    company_server.revoke(record.device_id)

    with pytest.raises(gateway.CompanyModelError) as refused:
        model.invoke([HumanMessage("Hello?")])

    words = "Your company disconnected this Row-Bot. Sign in again in Settings › Company."
    assert str(refused.value) == words
    assert store.read_record().disconnected is True and store.read_credential() is None
    from row_bot.agent import _friendly_api_error
    from row_bot.application.turn_failure import failure_reason

    assert failure_reason(_friendly_api_error(str(refused.value))) == words  # What the chat shows.
    assert backup.reason(refused.value) is None  # No other model answers for it.
    rows = _company_rows()
    assert set(rows) == {QWEN, CLAUDE}
    assert {row.runtime_state for row in rows.values()} == {"unavailable"}
    assert {row.status_reason for row in rows.values()} == {"Sign in again in Settings › Company."}
    (current,) = [option for option in client_status.picker_choices("chat", QWEN) if option["selection_ref"] == QWEN]
    assert current["available"] is False and current["reason"] == "Sign in again in Settings › Company."
    # Asked again, it fails the same way and sends nothing.
    asked = len(company_server.requests)
    with pytest.raises(gateway.CompanyModelError, match="Sign in again in Settings › Company"):
        model.invoke([HumanMessage("Hello?")])
    assert len(company_server.requests) == asked


def test_settings_says_how_many_models_and_the_companys_suggestion_and_leave_takes_them_away(
        company_server, lists):
    company_server.join_here()
    assert client_company.status()["model_count"] is None  # Not read yet.

    view = client_company.check()

    assert view["model_count"] == 2
    assert (view["suggested_model_ref"], view["suggested_model_name"]) == (QWEN, "Qwen3 32B")
    view = client_company.leave()
    assert view["model_count"] is None and view["suggested_model_ref"] is None
    assert _company_rows() == {}


def test_not_joined_there_is_no_company_provider_and_nothing_is_asked(company_server, lists):
    assert _company_rows() == {}
    assert not any(option["provider_id"] == "company" for option in client_status.picker_choices("chat"))
    assert client_company.status()["model_count"] is None
    with pytest.raises(gateway.CompanyModelError, match="isn't joined to a company"):
        create_chat_model(QWEN)
    provider.settle()
    assert company_server.requests == [] and company_server.gateway == []


@pytest.mark.parametrize("ref", [QWEN, CLAUDE])
def test_a_model_made_before_leave_sends_nothing_after_it(company_server, lists, ref):
    company_server.join_here()
    provider.refresh()
    model = create_chat_model(ref)  # Kept by the app's model cache, as a chat's model is.
    client_company.leave()
    asked = len(company_server.requests)

    with pytest.raises(gateway.CompanyModelError, match="isn't joined to a company"):
        model.invoke([HumanMessage("Hello?")])

    assert len(company_server.requests) == asked and company_server.gateway == []
