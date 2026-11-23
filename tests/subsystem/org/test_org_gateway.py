"""The model catalog and gateway end to end: the fake provider for sign-in, fake model endpoints on loopback
(tests/fixtures/model_endpoints.py), the server's routes and a real (throwaway) Postgres as the runtime role.

Covers a call through each wire format (whole and streamed) with the key added by the server, refused
models, hard and soft budgets, metering, embeddings, an endpoint timeout, keys never reaching a
response, a log or an error, and row-level security on usage. Security-sensitive: these run in the pull
request lane's Org job.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from fastapi.testclient import TestClient  # noqa: E402

from row_bot_org.app import create_app  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.db.rls import principal_transaction  # noqa: E402
from row_bot_org.logging import configure_logging  # noqa: E402
from row_bot_org.settings import load_settings  # noqa: E402
from tests.fixtures.model_endpoints import INPUT_TOKENS, KEY, OUTPUT_TOKENS, STREAM_WORDS, running_endpoint  # noqa: E402
from tests.fixtures.oidc_idp import CLIENT_SECRET, PUBLIC_URL, FakeIdp, running_idp  # noqa: E402
from tests.helpers.org_audit import stored_events, summary  # noqa: E402

pytestmark = pytest.mark.postgres

PEPPER = "a-test-pepper-that-is-long-enough-0123456789"
ORIGIN = {"Origin": PUBLIC_URL}
CHAT = "/api/gateway/openai/v1/chat/completions"
EMBEDDINGS = "/api/gateway/openai/v1/embeddings"
MESSAGES = "/api/gateway/anthropic/v1/messages"
HELLO = [{"role": "user", "content": "Hello"}]
# Per million tokens: 1200 in and 300 out cost 0.0036 + 0.0045.
PRICES = {"input_price": 3, "output_price": 15}
CALL_COST = 0.0081
# The beta flag Row-Bot's own Anthropic transport sends.
ANTHROPIC_BETA = "thinking-binding-controls-2026-08-01"


def new_gateway_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


GATEWAY_KEY = new_gateway_key()


@pytest.fixture
def idp() -> Iterator[FakeIdp]:
    with running_idp("okta") as running:
        yield running


# The provider's groups as the server knows them; "IT Admins" is mapped to the admin group "it-admins".
IT_ADMINS = "IT Admins"
GROUP_MAP = {IT_ADMINS: "it-admins", **{name: name for name in ("Sales", "Engineering", "Finance", "Execs")}}


@contextmanager
def org_server(database, idp: FakeIdp, gateway_key: str = GATEWAY_KEY, **env: str) -> Iterator[TestClient]:
    settings = load_settings({
        "ROW_BOT_ORG_IDP": json.dumps(idp.config(group_map=GROUP_MAP, admin_groups=["it-admins"])),
        "ROW_BOT_ORG_IDP_CLIENT_SECRET": CLIENT_SECRET,
        "ROW_BOT_ORG_PUBLIC_URL": PUBLIC_URL, "ROW_BOT_ORG_HMAC_PEPPER": PEPPER,
        "ROW_BOT_ORG_GATEWAY_KEY": gateway_key, **env,
    })
    pool = create_pool(database.runtime_url, max_size=4)
    with TestClient(create_app(settings, pool), base_url=PUBLIC_URL) as client:
        yield client


def person(server: TestClient, idp: FakeIdp, subject: str, groups: list[str] | None = None) -> TestClient:
    """A signed-in Row-Bot of its own (its own cookies), sending the server's origin as a same-origin client."""
    client = TestClient(server.app, base_url=PUBLIC_URL, headers=ORIGIN)
    login = client.get("/auth/login", follow_redirects=False)
    done = client.get(idp.authorize(login.headers["location"], subject=subject, groups=groups or []),
                      follow_redirects=False)
    assert done.status_code == 303, done.text
    return client


def admin_of(server: TestClient, idp: FakeIdp) -> TestClient:
    return person(server, idp, "it-admin", [IT_ADMINS])


def add_endpoint(admin: TestClient, base_url: str, api: str = "openai", **fields: Any) -> str:
    response = admin.post("/api/admin/endpoints", json={"name": f"{api}-{secrets.token_hex(3)}", "api": api,
                                                         "base_url": base_url, "api_key": KEY, **fields})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def add_model(admin: TestClient, endpoint_id: str, name: str, kind: str = "chat", **fields: Any) -> dict:
    response = admin.post("/api/admin/models", json={"endpoint_id": endpoint_id, "name": name, "kind": kind,
                                                      **fields})
    assert response.status_code == 201, response.text
    return response.json()


def usage(admin: TestClient, by: str = "model") -> list[dict]:
    response = admin.get(f"/api/admin/usage?by={by}")
    assert response.status_code == 200, response.text
    return response.json()["usage"]


def superuser_sql(database, sql: str, *args) -> list[tuple]:
    """As the cluster's superuser, past row-level security: what the test checks or arranges behind the server."""
    with psycopg.connect(database.superuser_url, autocommit=True) as conn:
        cursor = conn.execute(sql, args)
        return cursor.fetchall() if cursor.description else []


@pytest.fixture
def log_text() -> Iterator[Callable[[], str]]:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    yield stream.getvalue
    root.handlers[:], level = saved
    root.setLevel(level)


# -- calls ------------------------------------------------------------------------------------------

def test_a_chat_goes_through_with_the_key_added_and_no_key_reaches_the_client(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        add_model(admin, add_endpoint(admin, endpoint.base_url), "company-chat", upstream_model="llama-3.3-70b",
                  display_name="Company chat", context_window=131072, is_default=True)
        ada = person(server, idp, "ada")

        listed = ada.get("/api/models")
        assert listed.json() == {"models": [{"id": "company-chat", "display_name": "Company chat", "kind": "chat",
                                             "api": "openai", "context_window": 131072, "default": True}]}
        response = ada.post(CHAT, json={"model": "company-chat", "messages": HELLO})

        assert response.status_code == 200, response.text
        assert response.json()["choices"][0]["message"]["content"] == "".join(STREAM_WORDS)
        seen = endpoint.last()
        assert seen.path == "/v1/chat/completions"
        assert seen.headers["authorization"] == f"Bearer {KEY}"
        assert seen.body == {"model": "llama-3.3-70b", "messages": HELLO}
        # Nothing of the caller's reaches the endpoint: no cookie, no origin.
        assert "cookie" not in seen.headers and "origin" not in seen.headers
        # The key is nowhere the client can see.
        endpoints = admin.get("/api/admin/endpoints").json()["endpoints"]
        assert [e["has_key"] for e in endpoints] == [True]
        for answer in (response, listed, admin.get("/api/admin/endpoints"), admin.get("/api/admin/models")):
            assert KEY not in answer.text and KEY not in str(answer.headers)


def test_a_streamed_chat_passes_through_and_is_metered_from_its_last_chunk(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        add_model(admin, add_endpoint(admin, endpoint.base_url), "company-chat", **PRICES)
        ada = person(server, idp, "ada")

        response = ada.post(CHAT, json={"model": "company-chat", "messages": HELLO, "stream": True})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [line[len("data: "):] for line in response.text.splitlines() if line.startswith("data: ")]
        assert events[-1] == "[DONE]"
        words = [json.loads(e)["choices"][0]["delta"].get("content", "") for e in events[:-1]
                 if json.loads(e)["choices"]]
        assert "".join(words) == "".join(STREAM_WORDS)
        # The gateway asked for usage in the stream, and recorded it.
        assert endpoint.last().body["stream_options"] == {"include_usage": True}
        assert usage(admin) == [{"model": "company-chat", "calls": 1, "input_tokens": INPUT_TOKENS,
                                 "output_tokens": OUTPUT_TOKENS, "cost": CALL_COST}]
        ada_id = ada.get("/api/me").json()["id"]
    # Audited when the stream ended, with the usage row: counts only, never the prompt or the answer.
    (called,) = stored_events(org_database.owner_url, "model.called")
    duration = called["details"].pop("duration_ms")
    assert isinstance(duration, int) and duration >= 0
    assert summary(called) == ("model.called", "person", ada_id, None, "succeeded", {
        "provider": "openai", "model": "company-chat", "input_tokens": INPUT_TOKENS, "output_tokens": OUTPUT_TOKENS,
        "cost_micros": round(CALL_COST * 1_000_000)})
    assert "Hello" not in json.dumps(called) and STREAM_WORDS[0] not in json.dumps(called)


@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
def test_an_anthropic_message_goes_through_in_its_own_wire_format_and_is_metered(org_database, idp,
                                                                                 stream: bool) -> None:
    with running_endpoint("anthropic") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        add_model(admin, add_endpoint(admin, endpoint.base_url, api="anthropic"), "company-claude",
                  upstream_model="claude-sonnet-4-5", **PRICES)
        ada = person(server, idp, "ada")

        response = ada.post(MESSAGES, headers={"anthropic-beta": ANTHROPIC_BETA},
                            json={"model": "company-claude", "max_tokens": 100, "messages": HELLO, "stream": stream})

        assert response.status_code == 200, response.text
        if stream:
            names = [line[len("event: "):] for line in response.text.splitlines() if line.startswith("event: ")]
            assert names[0] == "message_start" and names[-1] == "message_stop"
        else:
            assert response.json()["content"][0]["text"] == "".join(STREAM_WORDS)
        seen = endpoint.last()
        assert seen.path == "/v1/messages" and seen.body["model"] == "claude-sonnet-4-5"
        assert seen.headers["x-api-key"] == KEY and "authorization" not in seen.headers
        assert seen.headers["anthropic-version"] == "2023-06-01"
        assert seen.headers["anthropic-beta"] == ANTHROPIC_BETA
        assert usage(admin) == [{"model": "company-claude", "calls": 1, "input_tokens": INPUT_TOKENS,
                                 "output_tokens": OUTPUT_TOKENS, "cost": CALL_COST}]


def test_an_embeddings_call_goes_through_and_is_metered(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        add_model(admin, add_endpoint(admin, endpoint.base_url), "company-embed", kind="embeddings",
                  input_price="0.02", is_default=True)
        ada = person(server, idp, "ada")

        response = ada.post(EMBEDDINGS, json={"model": "company-embed", "input": ["one", "two"]})

        assert response.status_code == 200, response.text
        assert [item["index"] for item in response.json()["data"]] == [0, 1]
        assert endpoint.last().path == "/v1/embeddings"
        assert usage(admin) == [{"model": "company-embed", "calls": 1, "input_tokens": INPUT_TOKENS,
                                 "output_tokens": 0, "cost": 0.000024}]


# -- refusals ---------------------------------------------------------------------------------------

def put_policy(admin: TestClient, document: dict, group: str | None = None) -> None:
    path = "/api/admin/policy" + ("" if group is None else f"/groups/{group}")
    view = admin.get("/api/admin/policy").json()
    version = (view["company"] if group is None else view["groups"].get(group, {})).get("version", 0)
    response = admin.put(path, json={"version": version, **document})
    assert response.status_code == 200, response.text


def test_a_model_the_person_may_not_use_is_refused_and_never_reaches_the_endpoint(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        endpoint_id = add_endpoint(admin, endpoint.base_url)
        add_model(admin, endpoint_id, "everyone-chat")
        add_model(admin, endpoint_id, "engineering-chat")
        add_model(admin, endpoint_id, "company-embed", kind="embeddings")
        # The policy is the one allow-list: the company allows two, Engineering's override adds a third.
        put_policy(admin, {"models": {"allowed": ["everyone-chat", "company-embed"]}})
        put_policy(admin, {"models": {"allowed": ["engineering-chat"]}}, "Engineering")
        sales, engineer = person(server, idp, "sam", ["Sales"]), person(server, idp, "eve", ["Engineering"])

        assert [m["id"] for m in sales.get("/api/models").json()["models"]] == ["everyone-chat", "company-embed"]
        assert {m["id"] for m in engineer.get("/api/models").json()["models"]} == {
            "everyone-chat", "engineering-chat", "company-embed"}
        for model in ("engineering-chat", "no-such-model"):
            refused = sales.post(CHAT, json={"model": model, "messages": HELLO})
            assert refused.status_code == 403
            assert refused.json() == {"error": {"message": "This model isn't available to you.",
                                                "type": "model_not_allowed", "code": "model_not_allowed"}}
        wrong_kind = sales.post(EMBEDDINGS, json={"model": "everyone-chat", "input": "x"})
        assert (wrong_kind.status_code, wrong_kind.json()["error"]["code"]) == (400, "wrong_model_kind")
        wrong_api = sales.post(MESSAGES, json={"model": "everyone-chat", "max_tokens": 10, "messages": HELLO})
        assert (wrong_api.status_code, wrong_api.json()["error"]["type"]) == (400, "wrong_api")
        assert endpoint.requests == []

        assert engineer.post(CHAT, json={"model": "engineering-chat", "messages": HELLO}).status_code == 200


def test_a_disabled_endpoint_hides_its_models_and_refuses_calls(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        endpoint_id = add_endpoint(admin, endpoint.base_url)
        add_model(admin, endpoint_id, "company-chat")
        ada = person(server, idp, "ada")

        assert admin.patch(f"/api/admin/endpoints/{endpoint_id}", json={"enabled": False}).status_code == 200
        assert ada.get("/api/models").json() == {"models": []}
        refused = ada.post(CHAT, json={"model": "company-chat", "messages": HELLO})
        assert (refused.status_code, refused.json()["error"]["code"]) == (503, "endpoint_disabled")
        assert endpoint.requests == []


def test_a_hard_budget_refuses_and_a_soft_one_warns(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        add_model(admin, add_endpoint(admin, endpoint.base_url), "company-chat", **PRICES)
        ada, bob = person(server, idp, "ada", ["Sales"]), person(server, idp, "bob", ["Sales"])
        ada_id = ada.get("/api/me").json()["id"]
        assert admin.put("/api/admin/budgets", json={"scope": "person", "subject": ada_id,
                                                     "hard_limit": "0.01"}).status_code == 200
        assert admin.put("/api/admin/budgets", json={"scope": "group", "subject": "Sales",
                                                     "soft_limit": "0.005"}).status_code == 200
        call = {"model": "company-chat", "messages": HELLO}

        first = ada.post(CHAT, json=call)
        assert first.status_code == 200 and "x-row-bot-budget-warning" not in first.headers
        second = ada.post(CHAT, json=call)  # the group has spent 0.0081 of its soft 0.005
        assert second.status_code == 200 and second.headers["x-row-bot-budget-warning"] == "group"
        third = ada.post(CHAT, json=call)  # Ada has spent 0.0162 of her hard 0.01
        assert third.status_code == 429
        assert third.json()["error"] == {"message": "Your monthly model budget is used up.",
                                         "type": "budget_exceeded", "code": "budget_exceeded"}
        assert len(endpoint.requests) == 2
        # Bob shares the group's soft budget, not Ada's hard one.
        assert bob.post(CHAT, json=call).headers["x-row-bot-budget-warning"] == "group"

        # A group's hard budget stops everyone in it; last month's spend doesn't count.
        admin.put("/api/admin/budgets", json={"scope": "group", "subject": "Sales", "hard_limit": "0.02"})
        refused = bob.post(CHAT, json=call)
        assert refused.json()["error"]["message"] == "The Sales group's monthly model budget is used up."
        superuser_sql(org_database, "UPDATE row_bot_org.usage_events SET created_at = date_trunc('month', now()) - "
                                "interval '1 day'")
        assert ada.post(CHAT, json=call).status_code == 200


@pytest.mark.slow
def test_an_endpoint_that_doesnt_answer_in_time_gives_a_clean_error(org_database, idp, log_text) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        endpoint_id = add_endpoint(admin, endpoint.base_url, name="gpu-box", timeout_seconds=1)
        add_model(admin, endpoint_id, "company-chat")
        ada = person(server, idp, "ada")
        endpoint.holding = True

        response = ada.post(CHAT, json={"model": "company-chat", "messages": HELLO})

        assert response.status_code == 504
        assert response.json()["error"] == {"message": "The company's model endpoint didn't answer in time.",
                                            "type": "endpoint_timeout", "code": "endpoint_timeout"}
        assert [(row[0], row[1]) for row in superuser_sql(
            org_database, "SELECT outcome, input_tokens FROM row_bot_org.usage_events")] == [("error", 0)]
    assert "model call to endpoint gpu-box failed (ReadTimeout)" in log_text()


# -- keys -------------------------------------------------------------------------------------------

def test_keys_never_appear_in_a_response_a_log_or_an_error(org_database, idp, log_text) -> None:
    wrong_key = "sk-wrong-key-that-the-endpoint-quotes-back-42"
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        answers = []
        # A refused body that carried a key.
        answers.append(admin.post("/api/admin/endpoints", json={"name": "no-api", "base_url": endpoint.base_url,
                                                                "api_key": KEY}))
        answers.append(admin.post("/api/admin/endpoints", json={"name": "bad", "api": "openai", "base_url": "ftp://x",
                                                                "api_key": KEY}))
        assert [a.status_code for a in answers] == [422, 422]
        good = add_endpoint(admin, endpoint.base_url)
        add_model(admin, good, "good-chat")
        wrong = add_endpoint(admin, endpoint.base_url, api_key=wrong_key)
        add_model(admin, wrong, "wrong-chat")
        ada = person(server, idp, "ada")

        # The endpoint refuses the wrong key, quoting it: the caller hears only that the server's key was refused.
        refused = ada.post(CHAT, json={"model": "wrong-chat", "messages": HELLO})
        assert (refused.status_code, refused.json()["error"]["code"]) == (502, "endpoint_auth_failed")
        # Another error that quotes the key is passed on without it.
        endpoint.error = 400
        failed = ada.post(CHAT, json={"model": "good-chat", "messages": HELLO, "stream": True})
        assert failed.status_code == 400 and "[redacted]" in failed.text
        endpoint.error = None
        answers += [refused, failed, admin.get("/api/admin/endpoints"),
                    admin.patch(f"/api/admin/endpoints/{good}", json={"api_key": KEY + "-rotated"}),
                    ada.post(CHAT, json={"model": "good-chat", "messages": HELLO})]
        assert answers[-1].status_code == 502  # the endpoint knows only the old key

        for answer in answers:
            for key in (KEY, wrong_key):
                assert key not in answer.text and key not in str(answer.headers)
        logs = log_text()
        assert logs and KEY not in logs and wrong_key not in logs
        # At rest the keys are sealed, not stored.
        stored = superuser_sql(org_database, "SELECT key_sealed FROM row_bot_org.model_endpoints")
        assert len(stored) == 2
        for (sealed,) in stored:
            assert KEY.encode() not in bytes(sealed) and wrong_key.encode() not in bytes(sealed)


def test_a_key_sealed_by_another_server_key_isnt_used(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint:
        with org_server(org_database, idp) as server:
            admin = admin_of(server, idp)
            add_model(admin, add_endpoint(admin, endpoint.base_url), "company-chat")
        with org_server(org_database, idp, gateway_key=new_gateway_key()) as server:
            response = person(server, idp, "ada").post(CHAT, json={"model": "company-chat", "messages": HELLO})

    assert (response.status_code, response.json()["error"]["code"]) == (503, "endpoint_key_unavailable")
    assert endpoint.requests == []


# -- the admin API ----------------------------------------------------------------------------------

def test_only_admins_manage_the_catalog_and_read_usage(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        ada = person(server, idp, "ada", ["Engineering"])
        for method, path in (("GET", "/api/admin/endpoints"), ("POST", "/api/admin/endpoints"),
                             ("GET", "/api/admin/models"), ("PUT", "/api/admin/budgets"),
                             ("GET", "/api/admin/usage")):
            answer = ada.request(method, path, json={"name": "x", "api": "openai", "base_url": endpoint.base_url})
            assert answer.status_code == 403, (method, path)
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.model_endpoints") == [(0,)]


def test_the_company_default_moves_and_an_endpoint_with_models_stays(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        endpoint_id = add_endpoint(admin, endpoint.base_url)
        first = add_model(admin, endpoint_id, "first-chat", is_default=True)
        second = add_model(admin, endpoint_id, "second-chat", is_default=True)
        embed = add_model(admin, endpoint_id, "company-embed", kind="embeddings", is_default=True)

        defaults = {m["name"]: m["is_default"] for m in admin.get("/api/admin/models").json()["models"]}
        assert defaults == {"first-chat": False, "second-chat": True, "company-embed": True}
        assert admin.patch(f"/api/admin/models/{first['id']}", json={"is_default": True}).json()["is_default"]
        defaults = {m["name"]: m["is_default"] for m in admin.get("/api/admin/models").json()["models"]}
        assert defaults == {"first-chat": True, "second-chat": False, "company-embed": True}

        assert admin.delete(f"/api/admin/endpoints/{endpoint_id}").json() == {"error": "endpoint_has_models"}
        for model in admin.get("/api/admin/models").json()["models"]:
            assert admin.delete(f"/api/admin/models/{model['id']}").status_code == 204
        assert admin.delete(f"/api/admin/endpoints/{endpoint_id}").status_code == 204
        assert second["endpoint_id"] == endpoint_id
        budget = admin.put("/api/admin/budgets", json={"scope": "group", "subject": "Sales", "hard_limit": 5})
        assert admin.delete(f"/api/admin/budgets/{budget.json()['id']}").status_code == 204
        admin_id = admin.get("/api/me").json()["id"]

    # Each catalog and budget change is an org settings change on what changed; a refusal records nothing.
    events = stored_events(org_database.owner_url, "org_settings.changed")
    assert {event["actor"]["id"] for event in events} == {admin_id}
    endpoint, budget_ref = f"gateway.endpoint.{endpoint_id}", f"gateway.budget.{budget.json()['id']}"
    one, two, three = (f"gateway.model.{model['id']}" for model in (first, second, embed))
    assert [(event["target"]["ref"], event["details"].get("change")) for event in events] == [
        (endpoint, "created"), (one, "created"), (two, "created"), (three, "created"), (one, "updated"),
        (one, "deleted"), (two, "deleted"), (three, "deleted"), (endpoint, "deleted"), (budget_ref, None),
        (budget_ref, "deleted")]


# -- what the gateway forwards ----------------------------------------------------------------------

def test_a_request_may_not_pick_other_models_or_send_a_stream_flag_that_isnt_a_boolean(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        endpoint_id = add_endpoint(admin, endpoint.base_url)
        add_model(admin, endpoint_id, "cheap-chat", upstream_model="openai/gpt-4o-mini")
        add_model(admin, endpoint_id, "exec-chat", upstream_model="anthropic/claude-opus-4")
        ada = person(server, idp, "ada", ["Sales"])
        call = {"model": "cheap-chat", "messages": HELLO}

        # The fields aggregators (OpenRouter, LiteLLM) pick other models with.
        for field, value in (("models", ["anthropic/claude-opus-4"]), ("route", "fallback"),
                             ("fallbacks", ["anthropic/claude-opus-4"]),
                             ("context_window_fallbacks", [{"cheap": ["anthropic/claude-opus-4"]}]),
                             ("provider", {"order": ["anthropic"]})):
            refused = ada.post(CHAT, json={**call, field: value})
            assert refused.status_code == 400, field
            assert refused.json()["error"]["code"] == "invalid_request" and field in refused.json()["error"]["message"]
        for stream in ("true", 1, None):
            refused = ada.post(CHAT, json={**call, "stream": stream})
            assert refused.json()["error"] == {"message": "stream must be true or false.", "type": "invalid_request",
                                               "code": "invalid_request"}
        assert endpoint.requests == []
        assert usage(admin) == []


def test_anthropic_server_tools_mcp_servers_and_other_betas_are_refused(org_database, idp) -> None:
    with running_endpoint("anthropic") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        add_model(admin, add_endpoint(admin, endpoint.base_url, api="anthropic"), "company-claude")
        ada = person(server, idp, "ada")
        call = {"model": "company-claude", "max_tokens": 10, "messages": HELLO}
        custom = {"name": "lookup", "description": "Look a word up.", "input_schema": {"type": "object"}}

        for extra, headers in (({"tools": [custom, {"type": "web_search_20250305", "name": "web_search"}]}, {}),
                               ({"tools": [{"type": "bash_20250124", "name": "bash"}]}, {}),
                               ({"tools": "web_search"}, {}),
                               ({"mcp_servers": [{"type": "url", "url": "https://mcp.example.test", "name": "x"}]}, {}),
                               ({}, {"anthropic-beta": "mcp-client-2025-04-04"}),
                               ({}, {"anthropic-beta": f"{ANTHROPIC_BETA},web-search-2025-03-05"})):
            refused = ada.post(MESSAGES, headers=headers, json={**call, **extra})
            assert refused.status_code == 400, (extra, headers)
            assert refused.json()["type"] == "error" and refused.json()["error"]["type"] == "invalid_request"
        assert endpoint.requests == []

        # Custom tools and Row-Bot's own beta flag go through.
        tools = [custom, {**custom, "name": "other", "type": "custom"}]
        assert ada.post(MESSAGES, headers={"anthropic-beta": ANTHROPIC_BETA},
                        json={**call, "tools": tools}).status_code == 200
        assert endpoint.last().body["tools"] == tools


def test_endpoint_errors_reach_the_caller_as_json_without_the_key_or_a_server_errors_body(org_database,
                                                                                          idp) -> None:
    with running_endpoint("anthropic") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        add_model(admin, add_endpoint(admin, endpoint.base_url, api="anthropic"), "company-claude")
        ada = person(server, idp, "ada")
        call = {"model": "company-claude", "max_tokens": 10, "messages": HELLO}

        endpoint.error = 400
        rejected = ada.post(MESSAGES, json=call)
        assert (rejected.status_code, rejected.headers["content-type"]) == (400, "application/json")
        assert rejected.json() == {"type": "error", "error": {"type": "invalid_request_error",
                                                              "message": "Something failed for key [redacted]"}}
        endpoint.error = 500
        failed = ada.post(MESSAGES, json={**call, "stream": True})
        assert (failed.status_code, failed.headers["content-type"]) == (502, "application/json")
        assert failed.json() == {"type": "error", "error": {"type": "endpoint_error",
                                                            "message": "The company's model endpoint failed."}}


def test_odd_answers_are_logged_by_endpoint_name_without_their_bodies(org_database, idp, log_text) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        add_model(admin, add_endpoint(admin, endpoint.base_url, name="gpu-box"), "company-chat",
                  upstream_model="llama-3.3-70b", **PRICES)
        ada = person(server, idp, "ada")

        endpoint.answer_model = "some-other-model"
        assert ada.post(CHAT, json={"model": "company-chat", "messages": HELLO}).status_code == 200
        endpoint.answer_model, endpoint.report_usage = None, False
        assert ada.post(CHAT, json={"model": "company-chat", "messages": HELLO}).status_code == 200

    logs = log_text()
    assert "endpoint gpu-box answered for model company-chat with another model" in logs
    assert "endpoint gpu-box reported no usage for priced model company-chat" in logs
    for body_text in ("some-other-model", "".join(STREAM_WORDS), "Hello"):
        assert body_text not in logs


def test_a_person_under_a_hard_budget_has_only_a_few_calls_at_once(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, \
            org_server(org_database, idp, ROW_BOT_ORG_GATEWAY_BUDGETED_CALLS="1") as server:
        admin = admin_of(server, idp)
        add_model(admin, add_endpoint(admin, endpoint.base_url), "company-chat", **PRICES)
        ada = person(server, idp, "ada")
        admin.put("/api/admin/budgets", json={"scope": "person", "subject": ada.get("/api/me").json()["id"],
                                              "hard_limit": "10"})
        call = {"model": "company-chat", "messages": HELLO}

        endpoint.holding = True
        held: list = []
        running = threading.Thread(target=lambda: held.append(ada.post(CHAT, json=call)))
        running.start()
        assert endpoint.arrived.acquire(timeout=10)
        busy = ada.post(CHAT, json=call)
        assert busy.status_code == 429 and busy.json()["error"]["code"] == "too_many_calls"

        endpoint.holding = False
        endpoint.hang.set()
        running.join(10)
        assert held[0].status_code == 502
        # Its slot came back with its end.
        assert ada.post(CHAT, json=call).status_code == 200
        assert len(endpoint.requests) == 2


# -- the endpoint's address and the admins ----------------------------------------------------------

def test_a_new_host_for_an_endpoint_never_gets_its_old_key(org_database, idp) -> None:
    with running_endpoint("openai") as real, running_endpoint("openai") as elsewhere, \
            org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        endpoint_id = add_endpoint(admin, real.base_url)
        add_model(admin, endpoint_id, "company-chat")
        call = {"model": "company-chat", "messages": HELLO}

        repointed = admin.patch(f"/api/admin/endpoints/{endpoint_id}", json={"base_url": elsewhere.base_url})
        assert (repointed.status_code, repointed.json()["has_key"]) == (200, False)
        refused = admin.post(CHAT, json=call)
        assert (refused.status_code, refused.json()["error"]["code"]) == (502, "endpoint_auth_failed")
        assert "authorization" not in elsewhere.last().headers

        # The key entered again with the new address goes there.
        rekeyed = admin.patch(f"/api/admin/endpoints/{endpoint_id}", json={"base_url": elsewhere.base_url,
                                                                            "api_key": KEY})
        assert rekeyed.json()["has_key"] is True
        assert admin.post(CHAT, json=call).status_code == 200
        assert elsewhere.last().headers["authorization"] == f"Bearer {KEY}"

        # An address changed behind the server's back: the sealed key doesn't open for the other host.
        superuser_sql(org_database, "UPDATE row_bot_org.model_endpoints SET base_url = %s", real.base_url)
        moved = admin.post(CHAT, json=call)
        assert (moved.status_code, moved.json()["error"]["code"]) == (503, "endpoint_key_unavailable")
        assert real.requests == []


def test_a_catalog_model_is_named_by_an_id_the_policy_can_allow(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        endpoint_id = add_endpoint(admin, "http://h.example.test/v1")
        for name in ("*", "two words", "-leading-dash", "x" * 201, "café"):
            refused = admin.post("/api/admin/models", json={"endpoint_id": endpoint_id, "name": name, "kind": "chat"})
            assert refused.status_code == 422, name
        model = add_model(admin, endpoint_id, "meta-llama/Llama-3.3-70B:fp8")
        assert admin.patch(f"/api/admin/models/{model['id']}", json={"name": "*"}).status_code == 422
        assert [m["name"] for m in admin.get("/api/admin/models").json()["models"]] == ["meta-llama/Llama-3.3-70B:fp8"]


def test_admin_refusals_never_echo_a_key(org_database, idp) -> None:
    with org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        answers = [admin.post("/api/admin/endpoints", json=body) for body in (
            {"api_key": KEY},
            {"name": "x", "api": "nope", "base_url": "http://h.example.test", "api_key": KEY},
            {"name": "x", "api": "openai", "base_url": KEY, "api_key": KEY},
            {"name": "x", "api": "openai", "base_url": "http://h.example.test", "api_key": KEY, KEY: 1},
            {"name": "x", "api": "openai", "base_url": "http://h.example.test", "api_key": [KEY]},
            # Keys go into a header: printable ASCII only.
            {"name": "x", "api": "openai", "base_url": "http://h.example.test", "api_key": KEY + "-café"},
        )]
        answers.append(admin.post("/api/admin/endpoints", content=b'{"api_key": "' + KEY.encode() + b'", ',
                                  headers={"content-type": "application/json"}))

        assert [answer.status_code for answer in answers] == [422] * 7
        for answer in answers:
            assert KEY not in answer.text and "café" not in answer.text
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.model_endpoints") == [(0,)]


# -- row-level security -----------------------------------------------------------------------------

def test_one_person_never_reads_anothers_usage_or_the_budgets(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        admin = admin_of(server, idp)
        add_model(admin, add_endpoint(admin, endpoint.base_url), "company-chat", **PRICES)
        add_model(admin, add_endpoint(admin, endpoint.base_url), "finance-chat")
        ada, bob = person(server, idp, "ada", ["Sales"]), person(server, idp, "bob", ["Sales"])
        ada_id, bob_id = ada.get("/api/me").json()["id"], bob.get("/api/me").json()["id"]
        admin.put("/api/admin/budgets", json={"scope": "group", "subject": "Sales", "soft_limit": "1"})
        for client in (ada, bob, bob):
            assert client.post(CHAT, json={"model": "company-chat", "messages": HELLO}).status_code == 200
        assert {row["person"]: row["calls"] for row in usage(admin, by="person")} == {ada_id: 1, bob_id: 2}
        assert usage(admin, by="group") == [{"group": "Sales", "calls": 3, "input_tokens": 3 * INPUT_TOKENS,
                                             "output_tokens": 3 * OUTPUT_TOKENS, "cost": 0.0243}]

    pool = create_pool(org_database.runtime_url, max_size=1)
    pool.open(wait=True, timeout=30)
    try:
        with principal_transaction(pool, ada_id, ["Sales"]) as conn:
            # No WHERE: row-level security alone keeps Ada to her own usage.
            assert conn.execute("SELECT principal_id::text FROM row_bot_org.usage_events").fetchall() == [(ada_id,)]
            assert conn.execute("SELECT count(*) FROM row_bot_org.budgets").fetchone() == (0,)
            # The catalog (the policy decides which of it she may use), and no change to any of it.
            assert conn.execute("SELECT count(*) FROM row_bot_org.model_endpoints").fetchone() == (2,)
            assert conn.execute("SELECT name FROM row_bot_org.catalog_models ORDER BY name").fetchall() == [
                ("company-chat",), ("finance-chat",)]
            assert conn.execute("UPDATE row_bot_org.catalog_models SET input_price = 0").rowcount == 0
            assert conn.execute("DELETE FROM row_bot_org.usage_events").rowcount == 0
            # Her group's budget, with what the whole group spent, and nothing else.
            assert conn.execute("SELECT scope, subject, spent FROM row_bot_org.current_budget_state()"
                                ).fetchall() == [("group", "Sales", Decimal("0.0243"))]
        with principal_transaction(pool, ada_id, ["Sales"]) as conn, \
                pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO row_bot_org.usage_events (principal_id, groups, model, input_tokens,"
                         " output_tokens, cost, duration_ms, outcome) VALUES (%s, '{}', 'x', 0, 0, 0, 0, 'ok')",
                         (bob_id,))
        with pool.connection() as conn:
            # Outside a principal's transaction: nothing, and no budget state.
            assert conn.execute("SELECT count(*) FROM row_bot_org.usage_events").fetchone() == (0,)
            assert conn.execute("SELECT count(*) FROM row_bot_org.current_budget_state()").fetchone() == (0,)
    finally:
        pool.close()
