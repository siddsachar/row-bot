from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from row_bot.plugins.api import PluginWebhookRequest, PluginWebhookResponse


pytestmark = pytest.mark.subsystem


class FakeWebhookRequest:
    def __init__(
        self,
        *,
        plugin_id: str = "teams-plugin",
        name: str = "events",
        method: str = "POST",
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
    ) -> None:
        self.path_params = {"plugin_id": plugin_id, "name": name}
        self.method = method
        self.headers = headers or {}
        self.query_params = query or {}
        self.url = SimpleNamespace(path=f"/plugin-webhooks/{plugin_id}/{name}")
        self.client = SimpleNamespace(host="127.0.0.1")
        self._body = body

    async def body(self) -> bytes:
        return self._body


def test_registering_webhook_returns_namespaced_path(
    plugin_modules: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhooks = plugin_modules["webhooks"]
    monkeypatch.setattr(webhooks, "_ensure_route_mounted", lambda: None)

    path = webhooks.register_plugin_webhook(
        "teams-plugin",
        "events",
        lambda request: PluginWebhookResponse(body="ok"),
    )

    assert path == "/plugin-webhooks/teams-plugin/events"
    assert webhooks.webhook_path("teams-plugin", "events") == path


def test_webhook_dispatch_translates_request_and_response(
    plugin_modules: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhooks = plugin_modules["webhooks"]
    monkeypatch.setattr(webhooks, "_ensure_route_mounted", lambda: None)
    seen: list[PluginWebhookRequest] = []

    def handler(request: PluginWebhookRequest) -> PluginWebhookResponse:
        seen.append(request)
        return PluginWebhookResponse(
            status_code=202,
            body="accepted",
            headers={"X-Test": "yes"},
        )

    webhooks.register_plugin_webhook("teams-plugin", "events", handler)
    response = asyncio.run(
        webhooks._dispatch_plugin_webhook(
            FakeWebhookRequest(
                body=b'{"type": "message"}',
                headers={"content-type": "application/json"},
                query={"validationToken": "abc"},
            )
        )
    )

    assert response.status_code == 202
    assert response.body == b"accepted"
    assert response.headers["x-test"] == "yes"
    assert seen[0].method == "POST"
    assert seen[0].path == "/plugin-webhooks/teams-plugin/events"
    assert seen[0].query == {"validationToken": "abc"}
    assert seen[0].json() == {"type": "message"}


def test_webhook_body_size_limit_is_enforced(
    plugin_modules: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhooks = plugin_modules["webhooks"]
    monkeypatch.setattr(webhooks, "_ensure_route_mounted", lambda: None)
    called: list[bool] = []
    webhooks.register_plugin_webhook(
        "teams-plugin",
        "events",
        lambda request: called.append(True) or PluginWebhookResponse(body="ok"),
        max_body_bytes=4,
    )

    response = asyncio.run(
        webhooks._dispatch_plugin_webhook(
            FakeWebhookRequest(body=b"too large", headers={"content-length": "9"})
        )
    )

    assert response.status_code == 413
    assert called == []


def test_unregistered_webhook_no_longer_dispatches(
    plugin_modules: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhooks = plugin_modules["webhooks"]
    monkeypatch.setattr(webhooks, "_ensure_route_mounted", lambda: None)
    called: list[bool] = []
    webhooks.register_plugin_webhook(
        "teams-plugin",
        "events",
        lambda request: called.append(True) or PluginWebhookResponse(body="ok"),
    )
    webhooks.unregister_plugin_webhooks("teams-plugin")

    response = asyncio.run(
        webhooks._dispatch_plugin_webhook(FakeWebhookRequest())
    )

    assert response.status_code == 404
    assert called == []


def test_duplicate_and_unsafe_webhook_names_are_rejected(
    plugin_modules: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhooks = plugin_modules["webhooks"]
    monkeypatch.setattr(webhooks, "_ensure_route_mounted", lambda: None)
    webhooks.register_plugin_webhook(
        "teams-plugin",
        "events",
        lambda request: PluginWebhookResponse(body="ok"),
    )

    with pytest.raises(ValueError, match="already registered"):
        webhooks.register_plugin_webhook(
            "teams-plugin",
            "events",
            lambda request: PluginWebhookResponse(body="ok"),
        )
    with pytest.raises(ValueError, match="must not contain slashes"):
        webhooks.register_plugin_webhook(
            "teams-plugin",
            "../events",
            lambda request: PluginWebhookResponse(body="ok"),
        )


def test_webhook_url_defaults_to_local_app_url(
    plugin_modules: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhooks = plugin_modules["webhooks"]
    monkeypatch.setenv("ROW_BOT_PORT", "8123")

    assert (
        webhooks.webhook_url("teams-plugin", "events")
        == "http://127.0.0.1:8123/plugin-webhooks/teams-plugin/events"
    )
