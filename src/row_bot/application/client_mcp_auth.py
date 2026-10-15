"""Explicit MCP sign-in/setup commands over admissions, config and secret owners."""
from __future__ import annotations

from collections.abc import Callable

import asyncio
import copy
from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import threading
import time
from urllib.parse import parse_qs, parse_qsl, urlsplit
from uuid import uuid4

from row_bot.application import capability_configuration_controls as configuration
from row_bot.mcp_client import auth, config, targets
from row_bot.runtime import admissions
from row_bot.developer.edits import FileEditRecovery

_LOCK = threading.RLock()
_FLOWS: dict[tuple[str, str], "Flow"] = {}
_TTL = 300
# How a sign-in's answer comes back. "this_computer" and "public": the service sends the browser to the callback
# route by itself (this computer's loopback address, or Row-Bot's one public HTTPS address). "paste": the browser
# is on another device and ends on an address that doesn't load there, which the person pastes back. "device":
# the person enters a code on the service's own page and Row-Bot asks for the result.
DELIVERIES = ("this_computer", "public", "paste", "device")
# Parts of the token exchange a pasted address never names: one that does is not the address the browser ended on.
_EXCHANGE = frozenset({"redirect_uri", "code_verifier", "code_challenge", "client_id", "client_secret", "grant_type",
                       "access_token", "refresh_token", "id_token", "token", "device_code"})


@dataclass
class Flow:
    owner_id: str
    command_id: str
    server_id: str
    name: str
    target: dict | None
    revision: str
    cfg: dict
    callback_uri: str
    validate: Callable[[], None]
    created: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)
    state: str = "starting"
    authorization_url: str = ""
    oauth_state: str = ""
    code: str = ""
    callback_used: bool = False
    document: bool = False  # Name Row-Bot by its client ID metadata document (the plan's choice).
    read_only: bool = False  # Ask only for the reads of what the server lists ("Look things up").
    ref: str = field(default_factory=lambda: uuid4().hex)
    # Its sign-in thread has ended: what it recorded is final, so its receipt and saved settings say how it ended.
    finished: bool = False
    delivery: str = "this_computer"  # One of DELIVERIES.
    expires_at: float = field(default_factory=lambda: time.time() + _TTL)  # Wall clock: only shown, never decides.
    device: dict | None = None  # The code and page a device sign-in shows while it waits.
    answered: str = ""  # The digest of the state its one answer brought, to tell a reused address apart.

    def active_authority(self) -> None:
        self.validate()
        if self.state in {"cancelled", "failed", "expired"} or time.monotonic() - self.created >= _TTL:
            raise auth.McpAuthError("mcp_auth_expired")

    def authority(self) -> None:
        self.active_authority()
        if configuration._revision(config.read_saved_configuration(self.target)) != self.revision:
            raise auth.McpAuthError("mcp_auth_configuration_changed")


def callback_uri(*, local_origin: str | None, public_origins: tuple[str, ...]) -> str:
    """Use a transport-proven loopback origin or an approved deployment origin."""
    from row_bot.access.config import canonical_origin
    if local_origin:
        value = canonical_origin(local_origin)
        if urlsplit(value).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise auth.McpAuthError("mcp_auth_callback_unavailable")
    else:
        values = [canonical_origin(v) for v in public_origins if v.startswith("https://")]
        if len(values) != 1:
            raise auth.McpAuthError("mcp_auth_callback_unavailable")
        value = values[0]
    return value + "/api/v1/settings/mcp/auth/callback"


def _server(server_id: str, target: dict | None):
    saved = config.read_saved_configuration(target)
    for name, cfg in saved.document.get("servers", {}).items():
        if configuration._server_id(name) == server_id:
            return saved, name, cfg
    raise auth.McpAuthError("mcp_auth_connection_unavailable")


def review_auth(*, server_id: str, configuration_revision: str, action: str, mode: str = "oauth",
        label: str = "", bindings: list | None = None, validate: Callable[[], None] = lambda: None,
        target: dict | None = None) -> dict:
    validate()
    target = targets.normalize(target)
    saved, name, cfg = _server(server_id, target)
    if configuration._revision(saved) != configuration_revision:
        raise auth.McpAuthError("revision_conflict")
    if action not in {"start", "disconnect"} or mode not in {"oauth", "api_key"}:
        raise auth.McpAuthError("invalid_mcp_auth")
    metadata = auth.validate_metadata({"mode": mode, "label": label, "bindings": bindings or []})
    if action == "start" and mode == "oauth":
        if cfg.get("transport", "stdio") == "stdio":
            raise auth.McpAuthError("mcp_oauth_http_required")
        auth.public_endpoint(cfg.get("url", ""))
    if mode == "api_key" and any(item["kind"] != "input" and (item["kind"] == "env") != (cfg.get("transport", "stdio") == "stdio")
                                 for item in metadata["bindings"]):
        raise auth.McpAuthError("invalid_mcp_auth")
    intent = {"server_id": server_id, "configuration_revision": configuration_revision,
        "action": action, "mode": mode, "label": label, "bindings": metadata["bindings"], "target": target}
    return {**intent, "action_digest": admissions.keyed_digest(intent)}


def _receipt(flow: Flow, *, message: str = "") -> dict:
    return {"command_id": flow.command_id, "server_id": flow.server_id, "state": flow.state,
        "message": message or {"starting": "Contacting the authorization service.", "waiting": "Continue sign-in in your browser.",
            "signed_in": "Signed in; test the connection and review its tools.",
            "uncertain": "Local binding cleared; owned runtime or credential cleanup is incomplete. Use Cancel or clear sign-in to retry cleanup of the original operation.",
            "disconnected": "Local account binding cleared. Remote revocation was not verified; revoke the app or token in the service's account security controls. Configuration and tool permissions are retained.", "cancelled": "Sign-in cancelled.",
            "expired": "Sign-in expired. Start a new sign-in.", "failed": "Sign-in failed; existing credentials were kept."}.get(flow.state, "Inspect connection setup."),
        "authorization_url": (flow.authorization_url or None) if flow.state == "waiting" else None,
        "delivery": flow.delivery,
        # While it waits: when its link or code stops working, whether its answer came (it is finishing), its code.
        "expires_at": flow.expires_at if flow.state in {"starting", "waiting"} else None,
        "received": flow.callback_used and flow.state == "waiting",
        "device": dict(flow.device) if flow.device and flow.state == "waiting" else None}


def _persist(flow: Flow, *, complete: bool = False, private: dict | None = None) -> dict:
    result = _receipt(flow)
    result.pop("authorization_url")
    result.pop("device")  # A code is shown only while its own sign-in runs, never kept.
    saved = admissions.read_command_receipt(flow.owner_id, flow.command_id) or {}
    result["_mcp_auth"] = {**saved.get("_mcp_auth", {}), "target": flow.target,
        "server_id": flow.server_id, "credential_ref": flow.ref, **(private or {})}
    if complete:
        admissions.complete_command(flow.owner_id, flow.command_id, result)
    else:
        admissions.command_progress(flow.owner_id, flow.command_id, result)
    return result


def _publish(flow: Flow, metadata: dict) -> None:
    with config.configuration_transaction():
        flow.authority()
        saved, name, _ = _server(flow.server_id, flow.target)
        document = copy.deepcopy(saved.document)
        document["servers"][name]["auth"] = metadata
        document["_client_publication"] = {"owner_id": flow.owner_id, "key": flow.command_id, "command_id": flow.command_id}
        private = {"credential_ref": flow.ref, "target": flow.target, "server_id": flow.server_id}
        def checkpoint(proof: FileEditRecovery) -> None:
            private["publication"] = asdict(proof)
            _persist(flow, private=private)
        config.publish_saved_configuration(document, expected_digest=saved.digest, command_id=flow.command_id,
            persist_recovery=checkpoint, validate=flow.active_authority, target=flow.target)


async def _run_oauth(flow: Flow, label: str, client: dict | None):
    """Sign in with the person's own client when they gave one; otherwise the server's authorization
    service picks Row-Bot's published client metadata (CIMD) when it supports it, else registers
    a client for this connection (DCR). The client and its secret stay in this connection's keychain entry."""
    storage = _storage(flow, client)
    url = _address(flow)
    async def redirect(url: str) -> None:
        flow.authority()
        auth.public_endpoint(url)
        states = parse_qs(urlsplit(url).query).get("state", [])
        if len(states) != 1 or not 20 <= len(states[0]) <= 512:
            raise auth.McpAuthError("mcp_auth_state_invalid")
        with _LOCK:
            flow.authorization_url, flow.oauth_state, flow.state = url, states[0], "waiting"
        _persist(flow)
    async def callback() -> tuple[str, str]:
        await asyncio.to_thread(flow.event.wait, max(0, _TTL - (time.monotonic() - flow.created)))
        flow.authority()
        if not flow.code:
            raise auth.McpAuthError("mcp_auth_denied")
        return flow.code, flow.oauth_state
    provider = auth.oauth_provider(url, flow.callback_uri, storage, redirect=redirect, callback=callback,
                                   client_metadata_url=auth.CLIENT_METADATA_URL if not client and flow.document else None,
                                   scope=str((flow.cfg.get("source") or {}).get("oauth_scope") or ""), read_only=flow.read_only)
    import httpx
    async with httpx.AsyncClient(auth=provider, timeout=30, follow_redirects=False, trust_env=False, transport=auth.PublicTransport()) as client_http:
        # This explicit unauthenticated request drives only authorization. Tool
        # discovery is a separate reviewed runtime command after sign-in.
        await client_http.post(url, headers={"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "Row-Bot", "version": "1"}}})
    _save_sign_in(flow, storage, label)


def _storage(flow: Flow, client: dict | None) -> auth.TokenStorage:
    """A staged sign-in for this connection, with the person's own OAuth app when they gave one."""
    storage = auth.TokenStorage(flow.ref, auth.binding(flow.name, flow.cfg), validate=flow.authority, staged=True)
    if client:
        from mcp.shared.auth import OAuthClientInformationFull
        storage.data["client"] = OAuthClientInformationFull.model_validate(client).model_dump(mode="json")
        storage.data["client_source"] = "own"
    return storage


def _address(flow: Flow) -> str:
    if flow.cfg.get("inputs"):  # An address with the person's own values (a tenant) filled in.
        from row_bot.integrations import inputs
        return inputs.resolve(flow.cfg, {}, partial=True)["url"]
    return flow.cfg["url"]


def _save_sign_in(flow: Flow, storage: auth.TokenStorage, label: str) -> None:
    flow.authority()
    if not storage.data.get("tokens"):
        raise auth.McpAuthError("mcp_auth_not_completed")
    auth.write_credentials(flow.ref, storage.data)
    # What the server granted when it says so, otherwise what was asked: either can be reads only.
    granted = str(storage.data["tokens"].get("scope") or getattr(storage, "asked", ""))
    reads = auth.limited(getattr(storage, "listed", ""), granted)
    _publish(flow, {"mode": "oauth", "credential_ref": flow.ref, "binding": storage.binding,
        "callback_uri": flow.callback_uri, "label": label, "operation_id": flow.command_id, **({"read_only": True} if reads else {})})


async def _run_device(flow: Flow, label: str, client: dict | None) -> bool:
    """Sign in with a code the person enters on the service's own page, on any device (RFC 8628). False when
    the service gives this client no code, before anything is shown: the browser sign-in carries on instead."""
    import httpx
    from row_bot.mcp_client import device_code
    storage = _storage(flow, client)
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False, transport=auth.PublicTransport()) as http:
        try:
            code = await device_code.start(http, _address(flow), storage, callback_uri=flow.callback_uri,
                                           scope=str((flow.cfg.get("source") or {}).get("oauth_scope") or ""),
                                           read_only=flow.read_only)
        except device_code.DeviceUnavailable:
            return False
        flow.authority()
        with _LOCK:
            flow.device = {"user_code": code.user_code, "verification_uri": code.verification_uri,
                           "verification_uri_complete": code.verification_uri_complete}
            flow.expires_at = min(flow.expires_at, time.time() + max(0.0, code.deadline - time.monotonic()))
            flow.state = "waiting"
        _persist(flow)
        try:
            await device_code.finish(http, code, storage, check=flow.authority,
                                     wait=lambda seconds: asyncio.to_thread(flow.event.wait, seconds))
        except auth.McpAuthError as error:
            if error.code == "mcp_auth_expired" and flow.state == "waiting":
                flow.state = "expired"  # The service's code ran out first: it expired, it didn't fail.
            raise
    _save_sign_in(flow, storage, label)
    return True


def _finish_oauth(flow: Flow, label: str, client: dict | None):
    try:
        if flow.delivery != "device" or not asyncio.run(_run_device(flow, label, client)):
            if flow.delivery == "device":
                with _LOCK:
                    flow.delivery = "paste"  # The service gave no code: sign in in the browser and paste back.
            asyncio.run(_run_oauth(flow, label, client))
        flow.state = "signed_in"
        old = flow.cfg.get("auth", {}).get("credential_ref")
        if old:
            auth.delete_bound_credentials(old, auth.binding(flow.name, flow.cfg))
        _persist(flow, complete=True)
    except Exception:
        # Publication can succeed before a state/receipt failure. Never remove
        # potentially active credentials or overwrite the retained edit proof.
        try:
            _, _, cfg = _server(flow.server_id, flow.target)
            if cfg.get("auth", {}).get("credential_ref") == flow.ref:
                flow.state = "signed_in"
            elif flow.state in {"starting", "waiting"} and time.monotonic() - flow.created >= _TTL:
                # Its own wait ran out with no answer from the browser: it expired, it didn't fail (B338).
                flow.state = "expired"
            elif flow.state not in {"cancelled", "expired"}:
                flow.state = "failed"
            _persist(flow, complete=True)
        except Exception:
            flow.state = "uncertain"
    finally:
        flow.code = flow.oauth_state = flow.authorization_url = ""
        flow.device = None
        flow.finished = True


def _running(owner_id: str, command_id: str) -> Flow | None:
    """The sign-in still running in this process for a command. One whose thread ended without recording its
    end (B338: its receipt couldn't be saved) is read like one a restart interrupted, so Retry can end it."""
    with _LOCK:
        flow = _FLOWS.get((owner_id, command_id))
    return flow if flow is not None and not flow.finished else None


def execute_auth(*, owner_id: str, command_id: str, server_id: str, configuration_revision: str,
        action: str, mode: str = "oauth", label: str = "", bindings: list | None = None,
        values: dict | None = None, client: dict | None = None, redirect_uri: str = "", metadata_document: bool = False,
        read_only: bool = False, validate: Callable[[], None] = lambda: None,
        validate_review: Callable[[dict], None] = lambda review: None, target: dict | None = None,
        delivery: str = "this_computer") -> dict:
    """``delivery``: how the sign-in's answer comes back (see ``DELIVERIES``). A pasted address or a code always
    uses ``auth.PASTE_CALLBACK``, the one address another device's browser is sent to."""
    validate()
    target = targets.normalize(target)
    if delivery not in DELIVERIES or (delivery in {"paste", "device"} and redirect_uri != auth.PASTE_CALLBACK):
        raise auth.McpAuthError("invalid_mcp_auth")
    intent = {"server_id": server_id, "configuration_revision": configuration_revision, "action": action,
        "mode": mode, "label": label, "bindings": bindings or [], "target": target}
    wire = {"command_id": command_id, "type": "mcp.auth." + action, **intent,
        "input_digest": admissions.keyed_digest([values, client, redirect_uri, metadata_document, *([True] if read_only else []),
                                                 *([delivery] if delivery != "this_computer" else [])])}
    existing = admissions.read_command_metadata(owner_id, command_id)
    if existing:
        try:
            replay = admissions.claim_command(owner_id, command_id, wire, targets.admission_target(target))
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise
            result = auth_status(owner_id=owner_id, command_id=command_id, validate=validate)
            _settle_recovered(owner_id, command_id, result)
            return result
        return {key: value for key, value in replay.items() if not key.startswith("_")} | {"authorization_url": None}
    review = review_auth(**intent, validate=validate)
    validate_review(review)
    config.require_configuration_write_available(target=target)
    saved, name, cfg = _server(server_id, target)
    flow = Flow(owner_id, command_id, server_id, name, target, configuration_revision, cfg, redirect_uri, validate,
                document=metadata_document, read_only=read_only, delivery=delivery)
    admissions.claim_command(owner_id, command_id, wire, targets.admission_target(target), exclusive_target=True,
        initial_result={"command_id": command_id, "server_id": server_id, "state": "starting",
            "_mcp_auth": {"target": flow.target, "server_id": server_id, "credential_ref": flow.ref}})
    if action == "disconnect":
        from row_bot.mcp_client import runtime
        lifetime = runtime.get_server_lifecycle(name)
        cleanup = {"name": name, "runtime_id": lifetime.get("runtime_id"),
            "ref": cfg.get("auth", {}).get("credential_ref"), "binding": auth.binding(name, cfg)}
        _persist(flow, private={"disconnect_cleanup": cleanup})
        _publish(flow, {"mode": "none", "label": label, "operation_id": command_id})
        try:
            _disconnect_cleanup(cleanup)
            flow.state = "disconnected"
            _persist(flow, complete=True, private={"disconnect_cleanup": None})
        except Exception:
            flow.state = "uncertain"
            _persist(flow)
    elif mode == "api_key":
        if type(values) is not dict or set(values) != {v["key"] for v in bindings or []} or not values or any(type(v) is not str or not v or len(v) > 16384 or "\r" in v or "\n" in v for v in values.values()):
            admissions.reject_command(owner_id, command_id, "invalid_mcp_auth")
            raise auth.McpAuthError("invalid_mcp_auth")
        flow.authority()
        bound = auth.binding(name, cfg)
        auth.write_credentials(flow.ref, {"binding": bound, "values": values})
        _publish(flow, {"mode": mode, "credential_ref": flow.ref, "binding": bound, "bindings": bindings, "label": label, "operation_id": command_id})
        flow.state = "signed_in"
        _persist(flow, complete=True)
        old = cfg.get("auth", {}).get("credential_ref")
        if old and old != flow.ref:
            auth.delete_bound_credentials(old, auth.binding(flow.name, flow.cfg))
    else:
        if not redirect_uri:
            admissions.reject_command(owner_id, command_id, "mcp_auth_callback_unavailable")
            raise auth.McpAuthError("mcp_auth_callback_unavailable")
        with _LOCK:
            # Only unfinished flows need memory; expired callbacks are invalid.
            for key, value in list(_FLOWS.items()):
                if time.monotonic() - value.created >= _TTL or value.state in {"signed_in", "failed", "cancelled", "expired"}:
                    _FLOWS.pop(key)
            if len(_FLOWS) >= 32:
                admissions.reject_command(owner_id, command_id, "mcp_auth_busy")
                raise auth.McpAuthError("mcp_auth_busy")
            _FLOWS[(owner_id, command_id)] = flow
        threading.Thread(target=_finish_oauth, args=(flow, label, client), daemon=True, name="mcp-sign-in").start()
    return _receipt(flow)


def auth_status(*, owner_id: str, command_id: str, validate: Callable[[], None] = lambda: None) -> dict:
    validate()
    flow = _running(owner_id, command_id)
    if flow:
        if flow.state in {"waiting", "starting"} and time.monotonic() - flow.created >= _TTL:
            return {**_receipt(flow), "state": "expired", "authorization_url": None, "message": "Sign-in expired. Start a new sign-in."}
        return _receipt(flow)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    result = admissions.read_command_receipt(owner_id, command_id)
    if not metadata or not metadata["type"].startswith("mcp.auth.") or not result:
        raise auth.McpAuthError("mcp_auth_flow_unavailable")
    if metadata["status"] != "completed":
        private = result.get("_mcp_auth", {})
        try:
            saved, _, cfg = _server(result["server_id"], targets.normalize(private.get("target")))
            published = saved.document.get("_client_publication", {})
            if (published.get("owner_id") == owner_id and published.get("command_id") == command_id
                    and cfg.get("auth", {}).get("operation_id") == command_id):
                # Observe the exact command marker, not a coincidentally
                # equal endpoint or token. No OAuth effect is replayed.
                if private.get("disconnect_cleanup"):
                    return {"command_id": command_id, "server_id": result["server_id"], "state": "uncertain", "authorization_url": None,
                        "message": "Local binding cleared; original cleanup is incomplete. Use Cancel or clear sign-in to finish owned cleanup."}
                return {"command_id": command_id, "server_id": result["server_id"],
                    "state": "disconnected" if cfg["auth"]["mode"] == "none" else "signed_in",
                    "authorization_url": None, "message": "Saved authentication recovered; test the connection."}
        except (ValueError, OSError):
            pass
        return {"command_id": command_id, "server_id": result["server_id"], "state": "expired", "authorization_url": None,
            "message": "The previous sign-in was interrupted. Cancel it, then start sign-in again."}
    return {key: value for key, value in result.items() if not key.startswith("_")} | {"authorization_url": None}


def _disconnect_cleanup(cleanup: dict) -> None:
    """Retry only cleanup of the original runtime and protected binding."""
    from row_bot.mcp_client import runtime
    if cleanup.get("runtime_id"):
        result = runtime.stop_server_owned(cleanup["name"], cleanup["runtime_id"])
        if result.get("state") != "stopped":
            raise auth.McpAuthError("mcp_cleanup_incomplete")
    if cleanup.get("ref"):
        auth.delete_bound_credentials(cleanup["ref"], cleanup["binding"])


def _settle_recovered(owner_id: str, command_id: str, result: dict) -> None:
    if _running(owner_id, command_id):
        return
    if result["state"] in {"signed_in", "disconnected", "cancelled"}:
        saved = admissions.read_command_receipt(owner_id, command_id) or {}
        admissions.complete_command(owner_id, command_id,
            {**saved, **{k: v for k, v in result.items() if k != "authorization_url"}})


def cancel_auth(*, owner_id: str, command_id: str, validate: Callable[[], None] = lambda: None) -> dict:
    result = auth_status(owner_id=owner_id, command_id=command_id, validate=validate)
    if result["state"] == "uncertain":
        saved = admissions.read_command_receipt(owner_id, command_id) or {}
        private = saved.get("_mcp_auth", {})
        if private.get("disconnect_cleanup"):
            validate()
            _disconnect_cleanup(private["disconnect_cleanup"])
            result.update(state="disconnected", message="Local binding and owned cleanup complete. Remote revocation was not verified; use the service's account security controls.")
            admissions.complete_command(owner_id, command_id, {**saved, **result, "_mcp_auth": {**private, "disconnect_cleanup": None}})
            return result
    with _LOCK:
        flow = _running(owner_id, command_id)
        if flow and flow.state in {"starting", "waiting"}:
            flow.state, flow.authorization_url = "cancelled", ""
            flow.event.set()
            result = _receipt(flow)
        elif not flow and result["state"] == "expired":
            result.update(state="cancelled", message="Interrupted sign-in cancelled. Review the connection before signing in again.")
    _settle_recovered(owner_id, command_id, result)
    if result["state"] == "cancelled" and flow:
        admissions.complete_command(owner_id, command_id, {k: v for k, v in result.items() if k != "authorization_url"})
    return result


def _digest(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _answer(flow: Flow, state: str, code: str, error: str) -> None:
    """Hand a waiting sign-in its one answer (holding ``_LOCK``): a code, or a refusal that ends it."""
    flow.authority()
    flow.callback_used, flow.answered = True, _digest(state)
    if error or not code:
        flow.state = "failed"
    else:
        flow.code = code
    flow.event.set()


def accept_callback(*, state: str, code: str = "", error: str = "") -> None:
    """The service sent a browser back to the callback route, with no session. Only a waiting sign-in whose
    browser returns by itself (this computer's, or one through Row-Bot's public address) takes it, once, by its
    one-time state; a sign-in finished by a pasted address or a code never does."""
    if not 20 <= len(state) <= 512 or len(code) > 4096 or len(error) > 256:
        raise auth.McpAuthError("mcp_auth_callback_invalid")
    with _LOCK:
        flow = next((f for f in _FLOWS.values() if f.oauth_state and hmac.compare_digest(f.oauth_state, state)), None)
        if (flow is None or flow.callback_used or flow.state != "waiting"
                or flow.delivery not in {"this_computer", "public"}):
            raise auth.McpAuthError("mcp_auth_callback_invalid")
        _answer(flow, state, code, error)


def pasted_answer(address: str, callback_uri: str) -> dict[str, str]:
    """The ``state`` and the ``code`` or ``error`` in an address pasted back from another device's browser.
    Nothing else in it is read. It must be exactly the sign-in's own callback address (scheme, host, port and
    path), carry each of those at most once, and name no part of the token exchange; it is never fetched."""
    value = address.strip().strip("<>\"'").strip()
    if value and "://" not in value[:12]:
        value = "http://" + value  # Copied without its scheme.
    if not value or len(value) > 8192 or any(ch.isspace() or ord(ch) < 0x20 or ch == "\\" for ch in value):
        raise auth.McpAuthError("mcp_auth_address_invalid")
    try:
        given, wanted = urlsplit(value), urlsplit(callback_uri)
        ports = {"http": 80, "https": 443}
        same = (given.scheme.lower() == wanted.scheme and given.hostname == wanted.hostname and given.path == wanted.path
                and (given.port or ports.get(given.scheme.lower())) == (wanted.port or ports[wanted.scheme]))
    except ValueError:  # An impossible port, or brackets that aren't an address.
        same = False
    if not same or given.username is not None or given.password is not None:
        raise auth.McpAuthError("mcp_auth_address_invalid")
    pairs = parse_qsl(given.query, keep_blank_values=True)
    names = [name for name, _ in pairs]
    if set(names) & _EXCHANGE or any(names.count(name) > 1 for name in ("state", "code", "error")):
        raise auth.McpAuthError("mcp_auth_address_invalid")
    found = {name: item for name, item in pairs if name in {"state", "code", "error"}}
    state, code, error = found.get("state", ""), found.get("code", ""), found.get("error", "")
    if not 20 <= len(state) <= 512 or not (code or error) or len(code) > 4096 or len(error) > 256:
        raise auth.McpAuthError("mcp_auth_address_invalid")
    return {"state": state, "code": code, "error": error}


def accept_pasted_callback(*, owner_id: str, command_id: str, address: str,
                           validate: Callable[[], None] = lambda: None) -> dict:
    """Finish a sign-in whose browser was on another device: the person pasted the address it ended on.

    Only this owner's own sign-in ``command_id``, one that asked for a pasted address, takes it, and it is
    checked exactly as the callback route checks one: this sign-in's one-time state (its PKCE verifier never
    leaves Row-Bot), once, before it expires, while its connection is unchanged. Each refusal says which.
    """
    validate()
    with _LOCK:
        flow = _FLOWS.get((owner_id, command_id))
    if flow is None or flow.delivery != "paste":
        raise auth.McpAuthError("mcp_auth_address_expired")  # Ended, or Row-Bot restarted since it began.
    answer = pasted_answer(address, flow.callback_uri)
    with _LOCK:
        if flow.callback_used:
            raise auth.McpAuthError("mcp_auth_address_used" if hmac.compare_digest(flow.answered, _digest(answer["state"]))
                                    else "mcp_auth_address_other")
        if flow.finished or flow.state != "waiting" or time.monotonic() - flow.created >= _TTL:
            raise auth.McpAuthError("mcp_auth_address_expired")
        if not hmac.compare_digest(flow.oauth_state.encode(), answer["state"].encode()):
            raise auth.McpAuthError("mcp_auth_address_other")
        try:
            _answer(flow, answer["state"], answer["code"], answer["error"])
        except auth.McpAuthError as error:
            if error.code == "mcp_auth_expired":
                raise auth.McpAuthError("mcp_auth_address_expired") from None
            raise
        return _receipt(flow)
