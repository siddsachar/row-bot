"""Twilio and plugin webhooks reach their own route checks through the access gate (B212).

Twilio and the services behind plugin webhooks call Row-Bot without a session or
an Origin header, from outside this computer (through the tunnel). The session
gate refused them with 401 before their route could check the Twilio signature
or the plugin's own credentials, so inbound SMS and plugin webhooks never
arrived. The gate now leaves exactly these routes to their handlers, as it does
for task webhooks; everything else still needs a session.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from row_bot.access.config import AccessConfig
from row_bot.access.middleware import AccessMiddleware


class NoSessions:
    def authenticate_scope(self, scope, provenance):  # noqa: ARG002
        return None


def _remote_client(reached: list[str]) -> TestClient:
    app = FastAPI()

    @app.post("/sms")
    async def sms(request: Request):
        reached.append(request.url.path)
        return PlainTextResponse("route decides", status_code=403)

    @app.api_route("/plugin-webhooks/{plugin_id}/{name}", methods=["GET", "POST"])
    async def plugin_webhook(request: Request, plugin_id: str, name: str):
        reached.append(request.url.path)
        return PlainTextResponse("route decides", status_code=403)

    @app.get("/api/private")
    async def private():
        reached.append("/api/private")
        return {"ok": True}

    middleware = AccessMiddleware(
        app,
        config=AccessConfig.build(deployment_mode="server", allowed_hosts=("localhost",)),
        session_authenticator=NoSessions(),
    )
    return TestClient(
        middleware,
        base_url="http://localhost:8080",
        client=("203.0.113.9", 51000),
        follow_redirects=False,
    )


def test_twilio_and_plugin_webhooks_reach_their_route_without_a_session() -> None:
    reached: list[str] = []
    client = _remote_client(reached)

    sms = client.post("/sms", data={"From": "+15550000000", "Body": "hello"},
                      headers={"X-Twilio-Signature": "not-checked-by-the-gate"})
    webhook = client.post("/plugin-webhooks/teams-channel/messages", json={})

    assert (sms.status_code, sms.text) == (403, "route decides")
    assert (webhook.status_code, webhook.text) == (403, "route decides")
    assert reached == ["/sms", "/plugin-webhooks/teams-channel/messages"]


def test_everything_else_still_needs_a_session() -> None:
    reached: list[str] = []
    client = _remote_client(reached)

    assert client.get("/api/private").status_code == 401
    assert client.get("/sms").status_code == 401
    assert reached == []
