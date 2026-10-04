"""Connection-scoped protected credentials and MCP SDK OAuth transport auth.

No browser, provider request or secure-store probe occurs on a settings read.
"""
from __future__ import annotations

import base64
import asyncio
import copy
import hashlib
import ipaddress
import json
import logging
import functools
import re
import socket
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from row_bot import secret_store

_NAMESPACE = "mcp_connections"
_REF = re.compile(r"[a-f0-9]{32}")
_LIMIT = 64 * 1024
_STORE_LOCK = threading.RLock()


def _serialized(function):
    @functools.wraps(function)
    def guarded(*args, **kwargs):
        with _STORE_LOCK:
            return function(*args, **kwargs)
    return guarded


class McpAuthError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def origin(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password or p.fragment:
        raise McpAuthError("mcp_auth_endpoint_invalid")
    return f"{p.scheme}://{p.netloc.lower()}"


def public_endpoint(url: str, *, resolve: bool = False) -> str:
    """OAuth discovery cannot reach loopback, private networks or HTTP origins."""
    value = origin(url)
    p = urlsplit(url)
    if p.scheme != "https" or p.port not in {None, 443} or p.hostname == "localhost" or p.hostname.endswith((".local", ".localhost")):
        raise McpAuthError("mcp_auth_endpoint_invalid")
    try:
        literal = ipaddress.ip_address(p.hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise McpAuthError("mcp_auth_endpoint_invalid")
    if resolve:
        addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise McpAuthError("mcp_auth_endpoint_invalid")
    return value


class PublicTransport(httpx.AsyncBaseTransport):
    """Resolve once and connect to that public IP, preserving TLS/Host identity."""
    def __init__(self):
        self.transports = {}

    async def handle_async_request(self, request):
        authority = public_endpoint(str(request.url))
        addresses = await asyncio.to_thread(socket.getaddrinfo, request.url.host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise McpAuthError("mcp_auth_endpoint_invalid")
        address = addresses[0][4][0]
        transport = self.transports.get(authority)
        if transport is None:
            transport = self.transports[authority] = httpx.AsyncHTTPTransport()
        pinned = httpx.Request(request.method, request.url.copy_with(host=address), headers=request.headers,
            stream=request.stream, extensions={**request.extensions, "sni_hostname": request.url.host})
        return await transport.handle_async_request(pinned)

    async def aclose(self):
        for transport in self.transports.values():
            await transport.aclose()


def binding(name: str, cfg: dict) -> str:
    # An endpoint/argv change cannot adopt credentials from another service.
    data = [name, cfg.get("source", {}), cfg.get("transport"), cfg.get("url", ""),
        cfg.get("command", ""), cfg.get("args", [])]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def validate_metadata(raw: dict | None) -> dict:
    if raw is None:
        return {}
    allowed = {"mode", "credential_ref", "binding", "label", "bindings", "callback_uri", "operation_id"}
    if type(raw) is not dict or set(raw) - allowed or raw.get("mode") not in {"none", "oauth", "api_key"}:
        raise McpAuthError("invalid_mcp_auth")
    for key in ("credential_ref", "operation_id"):
        if raw.get(key) and (type(raw[key]) is not str or not _REF.fullmatch(raw[key].replace("-", ""))):
            raise McpAuthError("invalid_mcp_auth")
    if type(raw.get("label", "")) is not str or len(raw.get("label", "")) > 128:
        raise McpAuthError("invalid_mcp_auth")
    if raw.get("binding") and not re.fullmatch(r"[a-f0-9]{64}", raw["binding"]):
        raise McpAuthError("invalid_mcp_auth")
    bindings = raw.get("bindings", [])
    if type(bindings) is not list or len(bindings) > 16:
        raise McpAuthError("invalid_mcp_auth")
    for value in bindings:
        if (type(value) is not dict or set(value) - {"kind", "name", "key", "prefix"}
                or value.get("kind") not in {"header", "env"}
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,127}", str(value.get("name", "")))
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", str(value.get("key", "")))
                or value.get("prefix", "") not in {"", "Bearer ", "Basic "}):
            raise McpAuthError("invalid_mcp_auth")
        if value["kind"] == "env" and value["name"].upper() in {"PATH", "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"}:
            raise McpAuthError("invalid_mcp_auth")
    return copy.deepcopy(raw)


def _index(ref: str) -> dict | None:
    if not _REF.fullmatch(ref):
        raise McpAuthError("invalid_credential_reference")
    raw = secret_store.get_secret(ref, namespace=_NAMESPACE)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        if not _REF.fullmatch(value["generation"]) or type(value["count"]) is not int or not 1 <= value["count"] <= 100:
            raise ValueError
        return value
    except (ValueError, KeyError, TypeError):
        raise McpAuthError("mcp_credentials_unavailable") from None


@_serialized
def read_credentials(ref: str) -> dict:
    value = _index(ref)
    if value is None or value.get("cleanup_binding"):
        raise McpAuthError("mcp_sign_in_required")
    pieces = [secret_store.get_secret(f"{ref}:{value['generation']}:{i}", namespace=_NAMESPACE) for i in range(value["count"])]
    try:
        raw = base64.b64decode("".join(pieces), validate=True)
        if len(raw) > _LIMIT or hashlib.sha256(raw).hexdigest() != value["digest"]:
            raise ValueError
        result = json.loads(raw)
        if type(result) is not dict:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise McpAuthError("mcp_credentials_unavailable") from None


def _delete_parts(ref: str, index: dict | None) -> None:
    if index:
        for i in range(index["count"]):
            secret_store.delete_secret(f"{ref}:{index['generation']}:{i}", namespace=_NAMESPACE)


@_serialized
def write_credentials(ref: str, data: dict) -> None:
    """Chunk below Windows Credential Manager limits; publish its pointer last."""
    before = _index(ref)
    raw = json.dumps(data, ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > _LIMIT:
        raise McpAuthError("mcp_credentials_too_large")
    encoded = base64.b64encode(raw).decode()
    chunks = [encoded[i:i + 900] for i in range(0, len(encoded), 900)]
    generation = uuid4().hex
    index = {"generation": generation, "count": len(chunks), "digest": hashlib.sha256(raw).hexdigest()}
    try:
        for i, part in enumerate(chunks):
            stored = secret_store.set_secret(f"{ref}:{generation}:{i}", part, namespace=_NAMESPACE)
            if stored not in {"keyring", "encrypted_file"}:
                raise McpAuthError("mcp_durable_storage_required")
        stored = secret_store.set_secret(ref, json.dumps(index), namespace=_NAMESPACE)
        if stored not in {"keyring", "encrypted_file"}:
            raise McpAuthError("mcp_durable_storage_required")
    except Exception:
        # A store can raise after accepting the pointer. Never destroy a
        # generation that may already be the active account.
        if _index(ref) != index:
            _delete_parts(ref, index)
        raise
    try:
        _delete_parts(ref, before)
    except secret_store.SecretStoreError:
        logging.getLogger(__name__).warning("Previous MCP credential generation needs cleanup")


@_serialized
def delete_credentials(ref: str) -> None:
    before = _index(ref)
    # The reference is first made unusable, even if a backend cleanup fails.
    secret_store.delete_secret(ref, namespace=_NAMESPACE)
    _delete_parts(ref, before)


@_serialized
def delete_bound_credentials(ref: str, expected_binding: str) -> bool:
    """Clear only this connection's binding; never delete a borrowed reference."""
    before = _index(ref)
    if before is None:
        return True
    if before.get("cleanup_binding"):
        if before["cleanup_binding"] != expected_binding:
            return False
    else:
        if read_credentials(ref).get("binding") != expected_binding:
            return False
        # A protected tombstone withdraws access before removing chunks and
        # retains their generation for restart-safe cleanup after a store error.
        before = {**before, "cleanup_binding": expected_binding}
        stored = secret_store.set_secret(ref, json.dumps(before), namespace=_NAMESPACE)
        if stored not in {"keyring", "encrypted_file"}:
            raise McpAuthError("mcp_durable_storage_required")
    _delete_parts(ref, before)
    secret_store.delete_secret(ref, namespace=_NAMESPACE)
    return True



class TokenStorage:
    """SDK TokenStorage, staging sign-in separately from a working account."""
    def __init__(self, ref: str, expected_binding: str, *, validate=lambda: None, staged: bool = False, data: dict | None = None):
        self.ref, self.binding, self.validate, self.staged = ref, expected_binding, validate, staged
        self.data = copy.deepcopy(data) if data is not None else ({"binding": expected_binding} if staged else read_credentials(ref))
        if self.data.get("binding") != expected_binding:
            raise McpAuthError("mcp_credentials_endpoint_changed")

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        raw = self.data.get("tokens")
        if not raw:
            return None
        raw = dict(raw)
        if self.data.get("expires_at"):
            raw["expires_in"] = max(0, int(self.data["expires_at"] - time.time()))
        return OAuthToken.model_validate(raw)

    async def set_tokens(self, tokens):
        self.validate()
        self.data["tokens"] = tokens.model_dump(mode="json")
        self.data["expires_at"] = time.time() + tokens.expires_in if tokens.expires_in is not None else None
        if not self.staged:
            from row_bot.mcp_client.config import configuration_transaction
            with configuration_transaction():
                self.validate()
                write_credentials(self.ref, self.data)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        raw = self.data.get("client")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, info):
        self.validate()
        self.data["client"] = info.model_dump(mode="json")
        if not self.staged:
            from row_bot.mcp_client.config import configuration_transaction
            with configuration_transaction():
                self.validate()
                write_credentials(self.ref, self.data)


class _SafeSdkLog(logging.Filter):
    def filter(self, record):
        record.msg, record.args, record.exc_info, record.exc_text = "MCP authorization step failed; sign in again from Integrations.", (), None, None
        return True


def oauth_provider(url: str, callback_uri: str, storage: TokenStorage, *, redirect=None, callback=None):
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata
    public_endpoint(url)
    sdk_logger = logging.getLogger("mcp.client.auth.oauth2")
    if not any(isinstance(value, _SafeSdkLog) for value in sdk_logger.filters):
        sdk_logger.addFilter(_SafeSdkLog())

    class BoundOAuth(OAuthClientProvider):
        async def async_auth_flow(self, request):
            flow = super().async_auth_flow(request)
            try:
                outgoing = await anext(flow)
                while True:
                    storage.validate()
                    public_endpoint(str(outgoing.url))
                    metadata = self.context.oauth_metadata
                    if metadata is not None:
                        expected = self.context.auth_server_url or origin(url)
                        if str(metadata.issuer).rstrip("/") != str(expected).rstrip("/"):
                            raise McpAuthError("mcp_auth_issuer_mismatch")
                        for endpoint in (metadata.authorization_endpoint, metadata.token_endpoint, metadata.registration_endpoint):
                            if endpoint:
                                public_endpoint(str(endpoint))
                        storage.data["issuer"] = expected
                        storage.data["metadata"] = metadata.model_dump(mode="json")
                    if outgoing.headers.get("authorization", "").startswith("Bearer ") and origin(str(outgoing.url)) != origin(url):
                        raise McpAuthError("mcp_auth_origin_mismatch")
                    response = yield outgoing
                    if 300 <= response.status_code < 400:
                        raise McpAuthError("mcp_auth_redirect_refused")
                    try:
                        outgoing = await flow.asend(response)
                    except StopAsyncIteration:
                        break
            finally:
                await flow.aclose()

    provider = BoundOAuth(url, OAuthClientMetadata(client_name="Row-Bot", redirect_uris=[callback_uri],
        grant_types=["authorization_code", "refresh_token"], response_types=["code"], token_endpoint_auth_method="none"),
        storage, redirect_handler=redirect, callback_handler=callback, timeout=300)
    if storage.data.get("metadata"):
        provider.context.oauth_metadata = OAuthMetadata.model_validate(storage.data["metadata"])
        provider.context.auth_server_url = storage.data["issuer"]
    # SDK 1.29 initializes saved tokens but not their expiry timestamp.
    provider.context.token_expiry_time = storage.data.get("expires_at")
    return provider


def transport_options(name: str, cfg: dict, *, validate=lambda: None) -> tuple[dict, dict]:
    """Resolve explicit secret bindings at use, leaving saved configs redacted."""
    effective = copy.deepcopy(cfg)
    metadata = validate_metadata(cfg.get("auth"))
    ref = metadata.get("credential_ref")
    options = {}
    if ref:
        if metadata.get("binding") != binding(name, cfg):
            raise McpAuthError("mcp_credentials_endpoint_changed")
        if metadata["mode"] == "oauth":
            def current_authority():
                validate()
                from row_bot.mcp_client import config
                source = cfg.get("source", {})
                if source.get("kind") == "plugin":
                    from row_bot.plugins.mcp import read_plugin_mcp_child
                    current = read_plugin_mcp_child(source["plugin_id"], source["server_id"])
                else:
                    current = config.read_saved_configuration().document.get("servers", {}).get(name, {})
                if current.get("auth") != cfg.get("auth") or binding(name, current) != metadata["binding"]:
                    raise McpAuthError("mcp_auth_configuration_changed")
            storage = TokenStorage(ref, metadata["binding"], validate=current_authority)
            options["auth"] = oauth_provider(cfg["url"], metadata["callback_uri"], storage)
        elif metadata["mode"] == "api_key":
            data = read_credentials(ref)
            if data.get("binding") != metadata["binding"]:
                raise McpAuthError("mcp_credentials_endpoint_changed")
            for item in metadata.get("bindings", []):
                value = data.get("values", {}).get(item["key"])
                if not isinstance(value, str):
                    raise McpAuthError("mcp_credentials_unavailable")
                effective.setdefault("headers" if item["kind"] == "header" else "env", {})[item["name"]] = item.get("prefix", "") + value
    if cfg.get("transport", "stdio") != "stdio":
        endpoint_origin = origin(cfg["url"])
        async def request_guard(request):
            validate()
            if options.get("auth"):
                public_endpoint(str(request.url))
            elif origin(str(request.url)) != endpoint_origin:
                raise McpAuthError("mcp_auth_origin_mismatch")
        def factory(headers=None, timeout=None, auth=None):
            return httpx.AsyncClient(headers=headers, timeout=timeout or 30, auth=auth,
                follow_redirects=False, trust_env=False, event_hooks={"request": [request_guard]},
                transport=PublicTransport() if options.get("auth") else None)
        options["httpx_client_factory"] = factory
    return effective, options
