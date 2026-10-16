"""The OAuth device authorization grant (RFC 8628) for a hosted app's sign-in finished on another device.

Row-Bot shows a short code and the service's own page. The person opens that page on any device, enters the
code and allows access, while Row-Bot asks the service's token endpoint until it answers. Every address Row-Bot
calls comes from the service's published metadata and is public HTTPS, checked as the browser sign-in checks it;
no redirect is followed, and nothing the person types or pastes is ever contacted.
"""
from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
import re
import time
from urllib.parse import quote

import httpx

from row_bot.mcp_client import auth

_LIMIT = 65536
_CODE = re.compile(r"[\x21-\x7e](?:[\x20-\x7e]{0,62}[\x21-\x7e])?")
_INITIALIZE = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
    "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "Row-Bot", "version": "1"}}}


class DeviceUnavailable(Exception):
    """The service gave this client no code: it offers none, or refused this client. Raised only before a code
    is shown, so the sign-in carries on in the browser instead."""


@dataclass
class DeviceStart:
    """A code the service issued, and how to finish it."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    interval: int
    deadline: float  # time.monotonic() when the service's own code expires.
    token_endpoint: str
    resource: str


async def _answer(response: httpx.Response) -> dict:
    """A JSON object answer, read only up to its cap; {} for anything else."""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > _LIMIT:
            return {}
    try:
        value = json.loads(bytes(body))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


async def _post(http: httpx.AsyncClient, url: str, **kwargs) -> tuple[int, dict]:
    auth.public_endpoint(url)
    headers = {"Accept": "application/json", **kwargs.pop("headers", {})}  # GitHub answers JSON only when asked.
    async with http.stream("POST", url, headers=headers, **kwargs) as response:
        if 300 <= response.status_code < 400:
            raise auth.McpAuthError("mcp_auth_redirect_refused")
        return response.status_code, await _answer(response)


def _client_auth(client, form: dict) -> tuple[dict, dict]:
    """The client's credentials as its registration says to send them (RFC 6749 section 2.3.1)."""
    method, secret = client.token_endpoint_auth_method, client.client_secret
    if method == "client_secret_basic" and secret:
        pair = f"{quote(client.client_id, safe='')}:{quote(secret, safe='')}"
        return form, {"Authorization": "Basic " + base64.b64encode(pair.encode()).decode()}
    if method == "client_secret_post" and secret:
        return {**form, "client_secret": secret}, {}
    return form, {}


async def _documents(http: httpx.AsyncClient, url: str):
    """The connection's protected resource metadata, its authorization server's metadata, and the scope its
    first answer named: one unauthenticated request to its own address, then its published documents."""
    from mcp.client.auth import utils
    from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata
    from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
    auth.public_endpoint(url)
    async with http.stream("POST", url, json=_INITIALIZE,
                           headers={"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"}) as first:
        if first.status_code != 401:
            raise DeviceUnavailable
        named, scope = utils.extract_resource_metadata_from_www_auth(first), utils.extract_scope_from_www_auth(first)
    protected = None
    for candidate in utils.build_protected_resource_metadata_discovery_urls(named, url):
        content = await auth._metadata(http, candidate)
        if content is not None:
            try:
                protected = ProtectedResourceMetadata.model_validate_json(content)
                break
            except ValueError:
                continue
    if protected is None:
        raise DeviceUnavailable
    resource = resource_url_from_server_url(url)
    if protected.resource and not check_resource_allowed(requested_resource=resource, configured_resource=str(protected.resource)):
        raise auth.McpAuthError("mcp_auth_issuer_mismatch")
    if protected.resource:
        resource = str(protected.resource)
    issuer = str(protected.authorization_servers[0])
    for candidate in utils.build_oauth_authorization_server_metadata_discovery_urls(issuer, url):
        content = await auth._metadata(http, candidate)
        if content is None:
            continue
        try:
            metadata = OAuthMetadata.model_validate_json(content)
        except ValueError:
            continue
        if str(metadata.issuer).rstrip("/") != issuer.rstrip("/"):
            raise auth.McpAuthError("mcp_auth_issuer_mismatch")
        for endpoint in (metadata.authorization_endpoint, metadata.token_endpoint, metadata.registration_endpoint):
            if endpoint:
                auth.public_endpoint(str(endpoint))
        return protected, metadata, content, issuer, scope, resource
    raise DeviceUnavailable


async def start(http: httpx.AsyncClient, url: str, storage: auth.TokenStorage, *, callback_uri: str,
                scope: str = "", read_only: bool = False) -> DeviceStart:
    """Ask the service for a code for this connection's sign-in. With no client yet (``storage`` carries the
    person's own), Row-Bot registers one that may use the device grant. ``scope`` and ``read_only`` work as in
    the browser sign-in. Raises ``DeviceUnavailable`` when no code comes, so the browser sign-in is used."""
    from mcp.client.auth import utils
    from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata
    protected, metadata, raw, issuer, named, resource = await _documents(http, url)
    endpoint = auth.device_endpoint(raw)
    if not endpoint:
        raise DeviceUnavailable
    storage.data["issuer"], storage.data["metadata"] = issuer, metadata.model_dump(mode="json")
    storage.listed = utils.get_client_metadata_scopes(named, protected, metadata) or ""
    storage.asked = scope or (read_only and auth.read_scope(storage.listed)) or storage.listed
    client = await storage.get_client_info()
    if client is None:
        if not metadata.registration_endpoint:
            raise DeviceUnavailable
        wanted = OAuthClientMetadata(client_name="Row-Bot", redirect_uris=[callback_uri], response_types=["code"],
                                     grant_types=["authorization_code", "refresh_token", auth.DEVICE_GRANT],
                                     token_endpoint_auth_method="none", scope=storage.listed or None)
        status, answer = await _post(http, str(metadata.registration_endpoint),
                                     json=wanted.model_dump(by_alias=True, mode="json", exclude_none=True))
        try:
            if status not in (200, 201):
                raise ValueError
            client = OAuthClientInformationFull.model_validate(answer)
        except ValueError:
            raise DeviceUnavailable from None  # It won't register a client for codes: sign in in the browser.
        await storage.set_client_info(client)
    form, headers = _client_auth(client, {"client_id": client.client_id, **({"scope": storage.asked} if storage.asked else {}),
                                          "resource": resource})
    status, answer = await _post(http, endpoint, data=form, headers=headers)
    complete = answer.get("verification_uri_complete")
    page = answer.get("verification_uri") or answer.get("verification_url")  # Google's older name.
    lasts, every = answer.get("expires_in"), answer.get("interval", 5)
    try:
        if status != 200 or "error" in answer or not isinstance(page, str) or not isinstance(answer.get("user_code"), str):
            raise ValueError
        if not isinstance(answer.get("device_code"), str) or not 0 < len(answer["device_code"]) <= 4096:
            raise ValueError
        if not _CODE.fullmatch(answer["user_code"]) or type(lasts) is not int or not 0 < lasts <= 3600:
            raise ValueError
        if type(every) is not int or not 0 < every <= 60:
            raise ValueError
        auth.public_endpoint(page)  # Shown and opened, never fetched: still only a public HTTPS page.
        if complete is not None and (not isinstance(complete, str) or len(complete) > 2048):
            raise ValueError
        if complete:
            auth.public_endpoint(complete)
    except ValueError:  # GitHub answers this way when its app has device flow switched off.
        raise DeviceUnavailable from None
    return DeviceStart(device_code=answer["device_code"], user_code=answer["user_code"], verification_uri=page[:2048],
                       verification_uri_complete=complete or "", interval=every, deadline=time.monotonic() + lasts,
                       token_endpoint=str(metadata.token_endpoint), resource=resource)


async def finish(http: httpx.AsyncClient, code: DeviceStart, storage: auth.TokenStorage, *,
                 wait: Callable[[float], Awaitable[None]], check: Callable[[], None]) -> None:
    """Ask the token endpoint, as often as the service allows, until the person allows or denies access or the
    code expires. ``wait`` returns early when the sign-in is stopped; ``check`` raises once it can't go on."""
    from mcp.shared.auth import OAuthToken
    client = await storage.get_client_info()
    every = code.interval
    while True:
        left = code.deadline - time.monotonic()
        if left <= 0:
            raise auth.McpAuthError("mcp_auth_expired")
        await wait(min(every, left))
        check()
        if time.monotonic() >= code.deadline:
            raise auth.McpAuthError("mcp_auth_expired")
        form, headers = _client_auth(client, {"grant_type": auth.DEVICE_GRANT, "device_code": code.device_code,
                                              "client_id": client.client_id, "resource": code.resource})
        status, answer = await _post(http, code.token_endpoint, data=form, headers=headers)
        problem = answer.get("error")
        if problem == "authorization_pending":
            continue
        if problem == "slow_down":
            every = min(every + 5, 60)
            continue
        if problem == "access_denied":
            raise auth.McpAuthError("mcp_auth_denied")
        if problem == "expired_token":
            raise auth.McpAuthError("mcp_auth_expired")
        if status != 200 or problem or not answer.get("access_token"):
            raise auth.McpAuthError("mcp_auth_not_completed")
        try:
            token = OAuthToken.model_validate(answer)
        except ValueError:
            raise auth.McpAuthError("mcp_auth_not_completed") from None
        check()
        await storage.set_tokens(token)
        return
