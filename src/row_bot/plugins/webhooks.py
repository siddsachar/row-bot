"""Internal registry for plugin-owned webhook routes."""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from row_bot.plugins.api import PluginWebhookRequest, PluginWebhookResponse

log = logging.getLogger("row_bot.plugins.webhooks")

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9\-]{1,63}$")
_WEBHOOK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_DEFAULT_METHODS = {"POST"}
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_MAX_BODY_LIMIT = 10 * 1024 * 1024
_ROUTE_TEMPLATE = "/plugin-webhooks/{plugin_id}/{name}"


@dataclass
class _WebhookRecord:
    handler: Callable[
        [PluginWebhookRequest],
        Awaitable[PluginWebhookResponse] | PluginWebhookResponse,
    ]
    methods: set[str]
    max_body_bytes: int


_webhooks: dict[tuple[str, str], _WebhookRecord] = {}
_route_mounted = False


def register_plugin_webhook(
    plugin_id: str,
    name: str,
    handler: Callable[
        [PluginWebhookRequest],
        Awaitable[PluginWebhookResponse] | PluginWebhookResponse,
    ],
    *,
    methods: list[str] | None = None,
    max_body_bytes: int = 1048576,
) -> str:
    """Register a plugin webhook handler and return its local path."""

    plugin_id = _validate_plugin_id(plugin_id)
    name = _validate_webhook_name(name)
    if not callable(handler):
        raise TypeError("handler must be callable")
    key = (plugin_id, name)
    if key in _webhooks:
        raise ValueError(f"Webhook route already registered for plugin '{plugin_id}': {name}")
    _ensure_route_mounted()
    _webhooks[key] = _WebhookRecord(
        handler=handler,
        methods=_normalize_methods(methods),
        max_body_bytes=_normalize_body_limit(max_body_bytes),
    )
    return webhook_path(plugin_id, name)


def unregister_plugin_webhooks(plugin_id: str) -> None:
    """Deactivate every webhook handler owned by *plugin_id*."""

    plugin_id = str(plugin_id or "")
    for key in [key for key in _webhooks if key[0] == plugin_id]:
        _webhooks.pop(key, None)


def webhook_path(plugin_id: str, name: str) -> str:
    """Return the namespaced webhook path for a plugin route."""

    return f"/plugin-webhooks/{_validate_plugin_id(plugin_id)}/{_validate_webhook_name(name)}"


def webhook_url(plugin_id: str, name: str, *, start_tunnel: bool = False) -> str:
    """Return a local or public URL for a plugin webhook route."""

    path = webhook_path(plugin_id, name)
    try:
        from row_bot.app_port import app_base_url, get_app_port

        app_port = get_app_port()
        if start_tunnel:
            try:
                from row_bot.tunnel import tunnel_manager

                public_url = tunnel_manager.get_url(app_port)
                if not public_url and tunnel_manager.is_available():
                    public_url = tunnel_manager.start_tunnel(app_port, label=f"plugin:{plugin_id}")
                if public_url:
                    return public_url.rstrip("/") + path
            except Exception:
                log.debug("Plugin webhook tunnel URL unavailable", exc_info=True)
        return app_base_url(port=app_port).rstrip("/") + path
    except Exception:
        return path


def _reset() -> None:
    """Clear webhook registrations for tests."""

    _webhooks.clear()


def _validate_plugin_id(plugin_id: str) -> str:
    value = str(plugin_id or "").strip()
    if not _PLUGIN_ID_RE.match(value):
        raise ValueError(f"Invalid plugin id for webhook route: {plugin_id!r}")
    return value


def _validate_webhook_name(name: str) -> str:
    value = str(name or "").strip().lower()
    if not _WEBHOOK_NAME_RE.match(value):
        raise ValueError(
            "Webhook route name must be lowercase alphanumeric plus '-' or '_' "
            "and must not contain slashes."
        )
    return value


def _normalize_methods(methods: list[str] | None) -> set[str]:
    normalized = {str(method).upper() for method in (methods or _DEFAULT_METHODS)}
    if not normalized:
        normalized = set(_DEFAULT_METHODS)
    unknown = normalized - _ALLOWED_METHODS
    if unknown:
        raise ValueError(f"Unsupported webhook method(s): {', '.join(sorted(unknown))}")
    return normalized


def _normalize_body_limit(max_body_bytes: int) -> int:
    try:
        value = int(max_body_bytes)
    except (TypeError, ValueError):
        value = 1048576
    if value < 0:
        value = 0
    return min(value, _MAX_BODY_LIMIT)


def _ensure_route_mounted() -> None:
    global _route_mounted
    if _route_mounted:
        return
    from nicegui import app as nicegui_app

    nicegui_app.add_route(
        _ROUTE_TEMPLATE,
        _dispatch_plugin_webhook,
        methods=sorted(_ALLOWED_METHODS),
    )
    _route_mounted = True


async def _dispatch_plugin_webhook(request: Any) -> Any:
    from starlette.responses import Response

    plugin_id = str(request.path_params.get("plugin_id") or "")
    name = str(request.path_params.get("name") or "")
    record = _webhooks.get((plugin_id, name))
    if record is None:
        return Response("Not found", status_code=404)
    method = str(request.method or "").upper()
    if method not in record.methods:
        return Response("Method not allowed", status_code=405)

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > record.max_body_bytes:
                return Response("Payload too large", status_code=413)
        except ValueError:
            pass
    body = await request.body()
    if len(body) > record.max_body_bytes:
        return Response("Payload too large", status_code=413)

    public_request = PluginWebhookRequest(
        method=method,
        path=str(request.url.path),
        query={str(k): str(v) for k, v in request.query_params.items()},
        headers={str(k): str(v) for k, v in request.headers.items()},
        body=body,
        client_host=str(getattr(request.client, "host", "") or ""),
    )
    try:
        response = record.handler(public_request)
        if inspect.isawaitable(response):
            response = await response
    except Exception:
        log.warning("Plugin webhook handler failed for %s/%s", plugin_id, name, exc_info=True)
        return Response("Webhook handler error", status_code=500)
    return _to_starlette_response(response)


def _to_starlette_response(response: Any) -> Any:
    from starlette.responses import Response

    if not isinstance(response, PluginWebhookResponse):
        response = PluginWebhookResponse(body=str(response or ""))
    return Response(
        content=response.body,
        status_code=int(response.status_code or 200),
        media_type=response.media_type or "text/plain",
        headers=dict(response.headers or {}),
    )
