"""Explicit subscription account actions over the retained provider auth owners."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    import httpx

import asyncio
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import threading
import time
from typing import Any
from urllib.parse import urlparse
import uuid

from row_bot.providers.config import load_provider_config, provider_config_revision, ProviderConfigError
from row_bot.runtime import admissions

PROVIDERS = ("codex", "claude_subscription", "xai_oauth")
ACTIONS = frozenset({"start", "check", "submit", "cancel", "disconnect", "restore", "import_token"})


class SubscriptionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SubscriptionAccountSnapshot:
    provider_id: str
    revision: str
    saved_state: str
    credential_storage: str
    has_recovery: bool
    expires_at: str | None
    runtime_state: str = "unknown"


@dataclass(frozen=True)
class SubscriptionAccountsSnapshot:
    schema_version: int
    revision: str
    accounts: tuple[SubscriptionAccountSnapshot, ...]


def _provider(provider_id: str) -> None:
    if provider_id not in PROVIDERS:
        raise SubscriptionError("not_found")


def _expiry(value: Any) -> str | None:
    try:
        if not isinstance(value, str) or len(value) > 80:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except ValueError:
        return None


def _account(provider_id: str, cfg: dict, revision: str) -> SubscriptionAccountSnapshot:
    entry = cfg.get("providers", {}).get(provider_id, {})
    configured = entry.get("configured")
    state = "metadata_only" if configured is True and entry.get("source") == "external_cli" else "disconnected" if not entry or configured is False or entry.get("oauth_bundle_ref") == {"cleared": True} else "saved" if configured is True else "unavailable"
    storage = entry.get("secret_storage")
    return SubscriptionAccountSnapshot(provider_id, revision, state,
        "durable" if storage in {"keyring", "encrypted_file"} else "session" if storage == "session" else "unknown",
        isinstance(entry.get("oauth_bundle_previous"), dict), _expiry(entry.get("expires_at")))


def read_accounts(*, validate: Callable[[], None] = lambda: None) -> SubscriptionAccountsSnapshot:
    validate()
    cfg = load_provider_config(strict=True)
    revision = provider_config_revision(cfg)
    result = SubscriptionAccountsSnapshot(1, revision, tuple(_account(provider, cfg, revision) for provider in PROVIDERS))
    validate()
    return result


def _value(operation: str, value: Any) -> str | None:
    if operation not in {"submit", "import_token"}:
        if value is not None:
            raise SubscriptionError("invalid_command")
        return None
    try:
        if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > 16384 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError
    except (ValueError, UnicodeError):
        raise SubscriptionError("invalid_command") from None
    return value


def review_action(provider_id: str, provider_revision: str, operation: str, value: str | None = None, *,
                  flow_id: str | None = None, server_epoch: str | None = None, validate: Callable[[], None]) -> dict:
    _provider(provider_id)
    if operation not in ACTIONS or (operation == "import_token" and provider_id != "claude_subscription"):
        raise SubscriptionError("invalid_command")
    value = _value(operation, value)
    if operation in {"check", "submit", "cancel"}:
        if not isinstance(flow_id, str) or len(flow_id) != 36 or not isinstance(server_epoch, str) or len(server_epoch) != 36:
            raise SubscriptionError("invalid_command")
    elif flow_id is not None or server_epoch is not None:
        raise SubscriptionError("invalid_command")
    snapshot = read_accounts(validate=validate)
    if snapshot.revision != provider_revision:
        raise SubscriptionError("revision_conflict")
    intent = {"provider_id": provider_id, "provider_revision": provider_revision, "operation": operation, "value": value,
              "flow_id": flow_id, "server_epoch": server_epoch}
    return {key: intent[key] for key in ("provider_id", "provider_revision", "operation", "flow_id", "server_epoch")} | {"action_digest": admissions.keyed_digest(intent)}


@dataclass(frozen=True)
class SubscriptionFlowSnapshot:
    flow_id: str
    server_epoch: str
    provider_id: str
    provider_revision: str
    state: str
    method: str
    authorization_url: str | None
    device_code: str | None
    expires_at: str | None
    quiescent: bool


class _BoundedClient:
    """Provider-compatible bounded requests; owns cancellation through completion."""
    def __init__(self, validate: Callable[[], None]):
        self.validate = validate
        self.lock = threading.Lock()
        self.loop = None
        self.task = None
        self.closed = False

    def close(self) -> None:
        with self.lock:
            self.closed = True
            if self.loop is not None and self.task is not None:
                try:
                    self.loop.call_soon_threadsafe(self.task.cancel)
                except RuntimeError:
                    # A request may have finished closing its loop immediately
                    # before its synchronous finally clears these references.
                    pass

    def _request(self, method, url, **kwargs):
        import httpx
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise SubscriptionError("subscription_endpoint_invalid")
        async def receive() -> httpx.Response:
            with self.lock:
                if self.closed:
                    raise SubscriptionError("subscription_cancelled")
                self.loop, self.task = asyncio.get_running_loop(), asyncio.current_task()
            self.validate()
            timeout = min(30.0, float(kwargs.pop("timeout", 30)))
            headers = {**kwargs.pop("headers", {}), "Accept-Encoding": "identity"}
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    async with client.stream(method, url, headers=headers, timeout=timeout, **kwargs) as response:
                        if response.headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
                            raise SubscriptionError("subscription_response_invalid")
                        data = bytearray()
                        async for chunk in response.aiter_raw():
                            self.validate()
                            if len(data) + len(chunk) > 256 * 1024:
                                raise SubscriptionError("subscription_response_invalid")
                            data.extend(chunk)
                        self.validate()
                        content_type = response.headers.get("content-type", "application/json")
                        content_type = "text/event-stream" if content_type.startswith("text/event-stream") else "application/json"
                        return httpx.Response(response.status_code, content=bytes(data), headers={"content-type": content_type}, request=response.request)
        try:
            return asyncio.run(receive())
        except asyncio.CancelledError:
            raise SubscriptionError("subscription_cancelled") from None
        finally:
            with self.lock:
                self.loop = self.task = None

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._request("POST", url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._request("GET", url, **kwargs)


@dataclass
class _Flow:
    flow_id: str
    owner_id: str
    provider_id: str
    revision: str
    deadline: float
    state: str = "starting"
    method: str = "authorization_code"
    flow: Any = None
    authorization: Any = None
    pending_tokens: Any = None
    pending_command: str | None = None
    start_command: str | None = None
    active: bool = False
    listening: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)
    listener_stop: threading.Event = field(default_factory=threading.Event)
    client: Any = None
    thread: threading.Thread | None = None
    listener_error: bool = False
    published: bool = False
    command_finished: bool = False
    operation_kind: str = "signin"
    probe_result: dict | None = None


class SubscriptionFlows:
    """Host-injected bounded lifecycle; all provider effects require explicit calls."""
    def __init__(self, *, client_factory: Callable = _BoundedClient, clock: Callable[[], float] = time.monotonic,
                 listener: Callable | None = None):
        self.server_epoch = str(uuid.uuid4())
        self._client_factory, self._clock, self._listener = client_factory, clock, listener
        self._lock = threading.RLock()
        self._flows: dict[str, _Flow] = {}
        self._closed = False

    def _lookup(self, owner_id: str, flow_id: str, epoch: str) -> _Flow:
        with self._lock:
            flow = self._flows.get(flow_id)
            if self._closed or epoch != self.server_epoch or flow is None or flow.owner_id != owner_id or flow.operation_kind != "signin":
                raise SubscriptionError("subscription_flow_unavailable")
            return flow

    def _snapshot(self, flow: _Flow) -> SubscriptionFlowSnapshot:
        with self._lock:
            public = flow.flow if not flow.cancel.is_set() and flow.state not in {"connected", "uncertain", "expired"} else None
            state = "connected" if flow.published else "draining" if flow.cancel.is_set() and (flow.active or flow.listening) else "cancelled" if flow.cancel.is_set() else "expired" if self._clock() >= flow.deadline and flow.state != "connected" else flow.state
            return SubscriptionFlowSnapshot(flow.flow_id, self.server_epoch, flow.provider_id, flow.revision, state, flow.method,
                getattr(public, "verification_uri", None) or getattr(public, "authorization_url", None),
                getattr(public, "user_code", None), _expiry(getattr(public, "expires_at", None)), not flow.active and not flow.listening)

    def read(self, *, owner_id: str, flow_id: str, server_epoch: str, validate: Callable[[], None]) -> SubscriptionFlowSnapshot:
        validate()
        result = self._snapshot(self._lookup(owner_id, flow_id, server_epoch))
        validate()
        return result

    def _guard(self, flow: _Flow, validate: Callable[[], None]):
        validate()
        if self._closed or flow.cancel.is_set():
            raise SubscriptionError("subscription_cancelled")
        if self._clock() >= flow.deadline:
            raise SubscriptionError("subscription_expired")
        if provider_config_revision(load_provider_config(strict=True)) != flow.revision:
            raise SubscriptionError("revision_conflict")
        if flow.pending_command:
            command = admissions.read_command_metadata(flow.owner_id, flow.pending_command)
            if command is not None and command["status"] == "rejected":
                raise SubscriptionError("subscription_cancelled")
        validate()
        if flow.cancel.is_set() or self._closed:
            raise SubscriptionError("subscription_cancelled")

    def _cancel(self, flow: _Flow):
        with self._lock:
            flow.cancel.set()
            flow.listener_stop.set()
            client = flow.client
            if not flow.active:
                flow.pending_tokens = flow.authorization = flow.flow = None
        if client is not None:
            client.close()

    def revoke_owner(self, owner_id: str) -> bool:
        with self._lock:
            retained = list(self._flows.values())
        for flow in retained:
            if flow.owner_id == owner_id:
                self._cancel(flow)
        with self._lock:
            return not any(flow.owner_id == owner_id and (flow.active or flow.listening) for flow in self._flows.values())

    def dispose(self) -> bool:
        with self._lock:
            self._closed = True
            retained = list(self._flows.values())
        for flow in retained:
            self._cancel(flow)
        return not self.has_pending()

    def has_pending(self) -> bool:
        with self._lock:
            return any(flow.active or flow.listening or (not flow.cancel.is_set() and flow.state not in {"connected", "expired"}) for flow in self._flows.values())

    @contextmanager
    def probe_operation(self, *, owner_id: str, command_id: str, provider_id: str,
                        revision: str, validate: Callable[[], None]) -> Iterator[tuple[_Flow, Callable[[], None]]]:
        """Share sign-in exclusion and shutdown with one explicit reviewed probe."""
        validate()
        _provider(provider_id)
        if not isinstance(command_id, str) or not command_id or len(command_id) > 128:
            raise SubscriptionError("invalid_command")
        with self._lock:
            if self._closed:
                raise SubscriptionError("subscription_flow_unavailable")
            if any(item.provider_id == provider_id and (item.active or item.listening or
                (not item.cancel.is_set() and item.state not in {"connected", "expired", "uncertain"})) for item in self._flows.values()):
                raise SubscriptionError("subscription_busy")
            if len(self._flows) >= 32:
                disposable = next((item for item in self._flows.values() if not item.active and not item.listening and
                    (item.cancel.is_set() or item.state == "connected")), None)
                if disposable is None:
                    raise SubscriptionError("subscription_capacity")
                self._flows.pop(disposable.flow_id)
            flow = _Flow(str(uuid.uuid4()), owner_id, provider_id, revision, self._clock() + 90,
                state="checking", active=True, pending_command=command_id, operation_kind="probe")
            self._flows[flow.flow_id] = flow
        try:
            yield flow, lambda: self._guard(flow, validate)
        except BaseException:
            flow.state = "uncertain"
            raise
        finally:
            try:
                if flow.client is not None:
                    flow.client.close()
            finally:
                with self._lock:
                    flow.active, flow.client = False, None
                    if flow.probe_result is not None:
                        flow.state = "connected"

    def probe_status(self, *, owner_id: str, command_id: str, validate: Callable[[], None]) -> dict | None:
        validate()
        with self._lock:
            flow = next((item for item in reversed(tuple(self._flows.values())) if item.operation_kind == "probe" and
                item.owner_id == owner_id and item.pending_command == command_id), None)
            if flow is None:
                return None
            result = {"command_id": command_id, "provider_id": flow.provider_id,
                "state": "completed" if flow.probe_result is not None else "draining" if flow.active and flow.cancel.is_set()
                    else "running" if flow.active else "cancelled" if flow.cancel.is_set() else "uncertain",
                "quiescent": not flow.active, "result": dict(flow.probe_result) if flow.probe_result is not None else None}
        validate()
        return result

    def cancel_probe(self, *, owner_id: str, command_id: str, validate: Callable[[], None]) -> dict:
        validate()
        with self._lock:
            flow = next((item for item in reversed(tuple(self._flows.values())) if item.operation_kind == "probe" and
                item.owner_id == owner_id and item.pending_command == command_id), None)
            if flow is None:
                raise SubscriptionError("subscription_flow_unavailable")
        self._cancel(flow)
        result = self.probe_status(owner_id=owner_id, command_id=command_id, validate=validate)
        return result

    def cancel_start(self, *, owner_id: str, command_id: str, validate: Callable[[], None]) -> SubscriptionFlowSnapshot:
        """Cancel the exact admitted Start even before its public handle returns."""
        validate()
        metadata = admissions.read_command_metadata(owner_id, command_id)
        if metadata is None or metadata["type"] != "provider.subscription.start" or metadata["target"] not in {f"subscription:{provider}" for provider in PROVIDERS}:
            raise SubscriptionError("subscription_flow_unavailable")
        provider_id = metadata["target"].removeprefix("subscription:")
        validate()
        with self._lock:
            flow = next((item for item in self._flows.values() if item.owner_id == owner_id and item.start_command == command_id), None)
            if flow is None:
                # The durable rejection fences an admitted Start still waiting
                # to reserve its in-memory flow; no parallel cancellation store.
                admissions.reject_command(owner_id, metadata["key"], "subscription_cancelled")
                if len(self._flows) >= 32:
                    raise SubscriptionError("subscription_capacity")
                flow = _Flow(str(uuid.uuid4()), owner_id, provider_id,
                    provider_config_revision(load_provider_config(strict=True)), self._clock(), state="cancelled",
                    start_command=command_id)
                self._flows[flow.flow_id] = flow
        self._cancel(flow)
        if not flow.published:
            admissions.reject_command(owner_id, metadata["key"], "subscription_cancelled")
        validate()
        return self._snapshot(flow)

    @staticmethod
    def _module(provider_id):
        from row_bot.providers import codex, claude_subscription, xai_oauth
        return {"codex": codex, "claude_subscription": claude_subscription, "xai_oauth": xai_oauth}[provider_id]

    def _start(self, flow: _Flow, validate):
        module = self._module(flow.provider_id)
        def guard() -> None:
            self._guard(flow, validate)
        guard()
        if flow.provider_id == "codex":
            flow.method = "device_code"
            flow.flow = module.start_codex_device_flow(http_client=flow.client)
        elif flow.provider_id == "claude_subscription":
            flow.flow = module.start_claude_subscription_oauth_flow()
        else:
            flow.method = "loopback"
            flow.flow = module.start_xai_oauth_flow(http_client=flow.client, persist_discovery=False)
        guard()
        url = getattr(flow.flow, "verification_uri", None) or getattr(flow.flow, "authorization_url", "")
        parsed = urlparse(url)
        if len(url) > 8192 or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise SubscriptionError("subscription_response_invalid")
        if len(getattr(flow.flow, "user_code", "") or "") > 128:
            raise SubscriptionError("subscription_response_invalid")
        if flow.provider_id == "xai_oauth":
            captured = flow.flow
            ready = threading.Event()
            listener = self._listener or module.wait_for_xai_oauth_loopback_authorization
            def listen() -> None:
                try:
                    authorization = listener(captured, open_browser=False, cancel_event=flow.listener_stop,
                        ready_callback=ready.set, timeout_seconds=max(0.05, flow.deadline - self._clock()), receive_timeout=1.0)
                    guard()
                    with self._lock:
                        if not flow.cancel.is_set():
                            flow.authorization = authorization
                except BaseException:
                    with self._lock:
                        flow.listener_error = True
                finally:
                    with self._lock:
                        flow.listening = False
                        if flow.cancel.is_set():
                            flow.authorization = flow.flow = None
            with self._lock:
                flow.listening = True
                flow.thread = threading.Thread(target=listen, name="subscription-loopback", daemon=True)
            try:
                flow.thread.start()
            except BaseException:
                flow.listening = False
                raise
            if not ready.wait(5):
                self._cancel(flow)
                raise SubscriptionError("subscription_listener_unavailable")
        flow.state = "waiting"

    def _publish(self, flow, tokens, proof, validate):
        module = self._module(flow.provider_id)
        name = "save_xai_oauth_tokens" if flow.provider_id == "xai_oauth" else f"save_{flow.provider_id}_oauth_tokens"
        getattr(module, name)(tokens, expected_revision=flow.revision,
            validate=lambda: self._guard(flow, validate), command_proof=proof)
        flow.published = True
        from row_bot.application.provider_settings_commands import invalidate_provider_runtime
        invalidate_provider_runtime()
        flow.pending_tokens = flow.authorization = flow.flow = None
        flow.state = "connected"
        # The loopback listener has already returned before Check can consume it.

    def _finish(self, flow, operation, value, proof, validate):
        module = self._module(flow.provider_id)
        def guard() -> None:
            self._guard(flow, validate)
        guard()
        if flow.pending_tokens is None:
            if flow.provider_id == "codex":
                if operation != "check":
                    raise SubscriptionError("invalid_command")
                flow.state = "checking"
                authorization = module.poll_codex_device_authorization(flow.flow, http_client=flow.client)
                guard()
                if authorization is None:
                    flow.state = "waiting"
                    return
            elif flow.provider_id == "claude_subscription":
                if operation != "submit":
                    raise SubscriptionError("invalid_command")
                saved = flow.flow
                authorization = module.ClaudeSubscriptionAuthorization(authorization_code=value, code_verifier=saved.code_verifier,
                    redirect_uri=saved.redirect_uri, token_url=saved.token_url, client_id=saved.client_id, state=saved.state)
            else:
                if operation == "submit":
                    # Never race the automatic callback consumer with a pasted code.
                    if flow.listening:
                        flow.listener_stop.set()
                        flow.thread.join(1.5)
                        if flow.listening:
                            raise SubscriptionError("subscription_listener_active")
                    guard()
                    authorization = module.authorization_from_xai_oauth_callback(flow.flow, value)
                else:
                    if flow.listening or flow.authorization is None:
                        flow.state = "waiting"
                        return
                    authorization = flow.authorization
            guard()
            flow.state = "exchanging"
            name = "exchange_codex_device_authorization" if flow.provider_id == "codex" else "exchange_claude_subscription_authorization" if flow.provider_id == "claude_subscription" else "exchange_xai_oauth_authorization"
            flow.pending_tokens = getattr(module, name)(authorization, http_client=flow.client)
            guard()
        flow.state = "publishing"
        self._publish(flow, flow.pending_tokens, proof, validate)

    def _result(self, command_id, flow=None):
        result = {"command_id": command_id, "status": "completed", "accounts": asdict(read_accounts())}
        if flow is not None:
            result["flow"] = asdict(self._snapshot(flow))
        return result

    @staticmethod
    def _durable(result):
        safe = {**result}
        if "flow" in safe:
            safe["flow"] = {**safe["flow"], "authorization_url": None, "device_code": None}
        return safe

    def execute(self, *, owner_id: str, key: str, command: dict, validate: Callable[[], None],
                validate_review: Callable[[dict], None]) -> dict:
        """Reserve Start lifecycle before any admission or policy wait."""
        validate()
        flow = None
        result = None
        if command.get("type") == "provider.subscription.start":
            payload = command.get("payload")
            if not isinstance(payload, dict):
                raise SubscriptionError("invalid_command")
            provider_id = payload.get("provider_id")
            _provider(provider_id)
            if not isinstance(command.get("command_id"), str) or not 1 <= len(command["command_id"]) <= 128:
                raise SubscriptionError("invalid_command")
            with self._lock:
                if self._closed:
                    raise SubscriptionError("subscription_flow_unavailable")
                flow = next((item for item in self._flows.values() if item.owner_id == owner_id and item.start_command == command.get("command_id")), None)
                if flow is not None and flow.active:
                    raise SubscriptionError("subscription_busy")
                if any(item is not flow and item.provider_id == provider_id and (item.active or item.listening or
                    (not item.cancel.is_set() and item.state not in {"connected", "expired", "uncertain"})) for item in self._flows.values()):
                    raise SubscriptionError("subscription_busy")
                if flow is None:
                    if len(self._flows) >= 32:
                        disposable = next((item for item in self._flows.values() if not item.active and not item.listening and (item.cancel.is_set() or item.state == "connected")), None)
                        if disposable is None:
                            raise SubscriptionError("subscription_capacity")
                        self._flows.pop(disposable.flow_id)
                    flow = _Flow(str(uuid.uuid4()), owner_id, provider_id, payload.get("provider_revision"), self._clock() + 600,
                        start_command=command.get("command_id"), pending_command=command.get("command_id"))
                    self._flows[flow.flow_id] = flow
                flow.active = True
        try:
            result = self._execute(owner_id=owner_id, key=key, command=command, validate=validate,
                validate_review=validate_review, _reserved=flow)
            return result
        finally:
            if flow is not None:
                with self._lock:
                    flow.active = False
                    if flow.state == "starting":
                        flow.cancel.set()
                    if result is not None and "flow" in result:
                        result["flow"] = asdict(self._snapshot(flow))

    def _execute(self, *, owner_id: str, key: str, command: dict, validate: Callable[[], None],
                 validate_review: Callable[[dict], None], _reserved: _Flow | None = None) -> dict:
        """Run one explicitly reviewed action; original identity never changes."""
        validate()
        payload = command.get("payload")
        operation = str(command.get("type", "")).removeprefix("provider.subscription.")
        if not isinstance(payload, dict) or operation not in ACTIONS or command.get("type") != f"provider.subscription.{operation}":
            raise SubscriptionError("invalid_command")
        provider_id = payload.get("provider_id")
        _provider(provider_id)
        value = _value(operation, payload.get("value"))
        command_id = command.get("command_id")
        if not isinstance(command_id, str) or len(command_id) > 128:
            raise SubscriptionError("invalid_command")
        proof = {"owner_id": owner_id, "key": key, "command_id": command_id}
        target = f"subscription:{provider_id}"
        flow = _reserved
        if operation in {"check", "submit", "cancel"}:
            flow = self._lookup(owner_id, payload.get("flow_id"), payload.get("server_epoch"))
            if flow.provider_id != provider_id:
                raise SubscriptionError("subscription_flow_unavailable")
        with self._lock:
            if self._closed:
                raise SubscriptionError("subscription_flow_unavailable")
            if operation != "cancel" and flow is not None and flow.active and flow is not _reserved:
                raise SubscriptionError("subscription_busy")
            if operation in {"start", "disconnect", "restore", "import_token"}:
                existing = next((item for item in self._flows.values() if item is not _reserved and item.provider_id == provider_id and
                    (item.active or item.listening or (not item.cancel.is_set() and item.state not in {"connected", "expired", "uncertain"}))), None)
                if existing is not None:
                    if existing.owner_id == owner_id and existing.pending_command == command_id and not existing.active and operation == "start":
                        flow = existing
                    else:
                        raise SubscriptionError("subscription_busy")
        # Claim only after the non-effectful owner checks, before any provider I/O.
        try:
            replay = admissions.claim_command(owner_id, key, command, target)
        except admissions.AdmissionError as exc:
            if str(exc) != "operation_uncertain":
                raise SubscriptionError(str(exc)) from None
            cfg = load_provider_config(strict=True)
            if cfg.get("providers", {}).get(provider_id, {}).get("oauth_bundle_command") == proof:
                validate()
                from row_bot.application.provider_settings_commands import invalidate_provider_runtime
                invalidate_provider_runtime()
                if flow is not None:
                    flow.pending_tokens = flow.authorization = flow.flow = None
                    flow.state = "connected"
                result = self._result(command_id, flow)
                admissions.complete_command(owner_id, key, self._durable(result))
                return result
            if flow is None or flow.pending_command != command_id or (flow.pending_tokens is None and not flow.command_finished):
                raise SubscriptionError("subscription_uncertain") from None
            replay = None
            if flow.command_finished:
                result = self._result(command_id, flow)
                admissions.complete_command(owner_id, key, self._durable(result))
                validate()
                return result
        if replay is not None:
            validate()
            if flow is not None:
                replay["flow"] = asdict(self._snapshot(flow))
            return replay
        try:
            reviewed = review_action(provider_id, payload.get("provider_revision"), operation, value,
                flow_id=payload.get("flow_id"), server_epoch=payload.get("server_epoch"), validate=validate)
            validate_review(reviewed)
        except Exception:
            admissions.reject_command(owner_id, key, "subscription_review_invalid")
            raise SubscriptionError("subscription_review_invalid") from None
        if operation == "cancel":
            self._cancel(flow)
            result = self._result(command_id, flow)
            admissions.complete_command(owner_id, key, self._durable(result))
            validate()
            return result
        if operation in {"start", "disconnect", "restore", "import_token"} and flow is None:
            with self._lock:
                # Repeat reservations after the durable admission wait.
                if any(item.provider_id == provider_id and (item.active or item.listening or (not item.cancel.is_set() and item.state not in {"connected", "expired", "uncertain"})) for item in self._flows.values()):
                    admissions.reject_command(owner_id, key, "subscription_busy")
                    raise SubscriptionError("subscription_busy")
                if len(self._flows) >= 32:
                    disposable = next((item for item in self._flows.values() if not item.active and not item.listening and (item.cancel.is_set() or item.state == "connected")), None)
                    if disposable is None:
                        raise SubscriptionError("subscription_capacity")
                    self._flows.pop(disposable.flow_id)
                flow = _Flow(str(uuid.uuid4()), owner_id, provider_id, payload["provider_revision"], self._clock() + 600)
                if operation == "start":
                    flow.start_command = command_id
                self._flows[flow.flow_id] = flow
        if flow is not None:
            with self._lock:
                if flow.cancel.is_set() or self._closed:
                    raise SubscriptionError("subscription_cancelled")
                if flow.active and flow is not _reserved:
                    raise SubscriptionError("subscription_busy")
                if flow.state == "uncertain" and flow.pending_tokens is None:
                    raise SubscriptionError("subscription_uncertain")
                flow.active, flow.pending_command = True, command_id
                flow.command_finished = False
        result = None
        try:
            flow.client = self._client_factory(lambda: self._guard(flow, validate))
            if operation == "start":
                self._start(flow, validate)
            elif operation in {"check", "submit"}:
                self._finish(flow, operation, value, proof, validate)
            else:
                self._account_action(provider_id, payload["provider_revision"], operation, value, proof,
                    lambda: self._guard(flow, validate))
                flow.state = "connected"
            flow.command_finished = True
            result = self._result(command_id, flow if operation in {"start", "check", "submit"} else None)
            admissions.complete_command(owner_id, key, self._durable(result))
            validate()
            return result
        except BaseException as exc:
            if isinstance(exc, SubscriptionError) and exc.code == "subscription_cancelled" and flow is not None and not flow.published:
                admissions.reject_command(owner_id, key, exc.code)
            if isinstance(exc, SubscriptionError) and exc.code in {"invalid_command", "subscription_listener_active"} and flow is not None and flow.state == "waiting":
                admissions.reject_command(owner_id, key, exc.code)
            if flow is not None and flow.state not in {"connected", "waiting"}:
                flow.state = "uncertain"
            code = exc.code if isinstance(exc, (SubscriptionError, ProviderConfigError)) else "subscription_uncertain"
            raise SubscriptionError(code) from None
        finally:
            if flow is not None:
                try:
                    if flow.client is not None:
                        flow.client.close()
                finally:
                    with self._lock:
                        flow.client, flow.active = None, False
                        if flow.cancel.is_set():
                            flow.pending_tokens = flow.authorization = flow.flow = None
                        if result is not None and "flow" in result:
                            result["flow"] = asdict(self._snapshot(flow))

    def _account_action(self, provider_id, revision, operation, value, proof, validate):
        from row_bot.providers import auth_store
        module = self._module(provider_id)
        def guard() -> None:
            validate()
            if self._closed:
                raise SubscriptionError("subscription_cancelled")
            if provider_config_revision(load_provider_config(strict=True)) != revision:
                raise SubscriptionError("revision_conflict")
        guard()
        if operation == "disconnect":
            getattr(module, f"disconnect_{provider_id}_metadata")(expected_revision=revision, validate=guard, command_proof=proof)
        elif operation == "import_token" and provider_id == "claude_subscription":
            metadata = module.claude_subscription_token_metadata(value)
            tokens = module.ClaudeSubscriptionTokenSet(access_token=value,
                expires_at=str(metadata.get("expires_at") or ""), user_id=str(metadata.get("user_id") or ""),
                account_id=str(metadata.get("account_id") or ""), plan_type=str(metadata.get("plan_type") or ""), scopes=tuple(metadata.get("scopes") or ()))
            module.save_claude_subscription_oauth_tokens(tokens, expected_revision=revision, validate=guard, command_proof=proof)
        elif operation == "restore":
            from row_bot.providers.config import provider_config_transaction
            with provider_config_transaction():
                guard()
                previous = load_provider_config(strict=True).get("providers", {}).get(provider_id, {}).get("oauth_bundle_previous")
                if not isinstance(previous, dict) or not isinstance(previous.get("metadata"), dict):
                    raise SubscriptionError("subscription_recovery_unavailable")
                reference = previous.get("reference")
                values = {name: auth_store._get_provider_secret_value(provider_id, name) for name in auth_store.OAUTH_SECRET_NAMES} if reference == {"legacy": True} else auth_store._oauth_bundle_values(provider_id, reference)
                if not values.get("access_token"):
                    raise SubscriptionError("subscription_recovery_unavailable")
                auth_store.replace_provider_oauth_bundle(provider_id, values,
                    update_metadata=lambda cfg: cfg.setdefault("providers", {}).__setitem__(provider_id, dict(previous["metadata"])),
                    expected_revision=revision, validate=guard, command_proof=proof)
        else:
            raise SubscriptionError("invalid_command")
        from row_bot.application.provider_settings_commands import invalidate_provider_runtime
        invalidate_provider_runtime()


def read_receipt(*, provider_id: str, owner_id: str, command_id: str, validate: Callable[[], None]) -> dict | None:
    _provider(provider_id)
    validate()
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if metadata is None or metadata["target"] != f"subscription:{provider_id}" or metadata["type"] not in {f"provider.subscription.{action}" for action in ACTIONS}:
        return None
    cfg = load_provider_config(strict=True)
    published = cfg.get("providers", {}).get(provider_id, {}).get("oauth_bundle_command") == {"owner_id": owner_id, "key": metadata["key"], "command_id": command_id}
    result = {"command_id": command_id, "status": "completed" if metadata["status"] == "completed" or published else "rejected" if metadata["status"] == "rejected" else "uncertain",
        "published": published, "accounts": asdict(read_accounts(validate=validate))}
    validate()
    return result
