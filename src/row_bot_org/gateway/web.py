"""The gateway's routes.

For connected Row-Bots (any signed-in principal, with a browser session or a device's bearer token; a bearer's
device is named on its calls' ``model.called``):

- ``GET /api/models``: the models this person may use;
- ``POST /api/gateway/openai/v1/chat/completions`` and ``.../embeddings``: OpenAI wire format;
- ``POST /api/gateway/anthropic/v1/messages``: Anthropic wire format.

A call is refused (in the caller's wire format) when the model isn't one the person may use, belongs
to the other API, is the wrong kind, sits on a disabled endpoint, or when the person's or one of
their groups' hard monthly budget is spent. A soft budget reached adds ``X-Row-Bot-Budget-Warning``
(``person``, ``group`` or both). The server sends the endpoint's key, passes streams through as they
come, and records one usage row per call it forwards, from the endpoint's own usage fields, with its
``model.called`` audit event (counts only: tokens, cost, duration and outcome) in the same short transaction
when the call ends (a stream's event can't share the transaction that let the call start).

The company brain (``row_bot_org.brain``) embeds in process through ``GatewayService.embed``, with the company's
default embeddings model, metered and budgeted as a call of the person it embeds for (the uploader, or who
recalls). That model is the brain's, not a person's choice, so their ``models.allowed`` doesn't apply to it.

A person may use the catalog models their effective policy allows (``models.allowed``, with their groups'
overrides): ``/api/models`` lists only those, and a call to any other is refused as unknown.

For admins (``row_bot_org.identity.roles``): ``/api/admin/endpoints``, ``/api/admin/models``,
``/api/admin/budgets`` and ``GET /api/admin/usage``. An endpoint's key goes in once and never comes
back out: responses say only ``has_key``. Each change is audited as ``org_settings.changed`` on the setting
``gateway.endpoint.<id>``, ``gateway.model.<id>`` or ``gateway.budget.<id>``.

Never logged: request or response bodies, keys, the endpoint's error text. Logged: the endpoint's name
and the failure's kind.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import anyio
import httpx
import psycopg
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from starlette.concurrency import run_in_threadpool

from row_bot.audit import Action, InvalidAuditEvent, Outcome, Target, TargetKind
from row_bot.policy.model import CATALOG_MODEL_ID
from row_bot_org import audit
from row_bot_org.db.rls import principal_transaction
from row_bot_org.gateway import store, upstream
from row_bot_org.gateway.keys import KeyUnavailable, endpoint_origin, seal, unseal
from row_bot_org.identity.gate import RequestPrincipal, request_principal
from row_bot_org.identity.roles import admin_connection
from row_bot_org.identity.web import principal_connection
from row_bot_org.policy import store as policy_store
from row_bot_org.settings import OrgSettings

logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
BUDGET_WARNING_HEADER = "X-Row-Bot-Budget-Warning"
_MILLION = Decimal(1_000_000)
_COST_PLACES = Decimal("0.00000001")


class _Refusal(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message


# The largest vector pgvector stores.
MAX_EMBEDDING_DIMENSIONS = 16_000


class NoEmbeddingsModel(Exception):
    """The catalog has no default embeddings model (on the OpenAI wire): the company brain searches text only."""


class EmbeddingsFailed(Exception):
    """The default embeddings model couldn't embed these texts; ``message`` says why, for a person to read."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _vectors(answer: Any, count: int) -> list[list[float]]:
    """The ``count`` embeddings of an OpenAI-wire answer, in input order, all of one dimension; ValueError when
    the answer isn't that."""
    data = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(data, list) or len(data) != count:
        raise ValueError("the answer doesn't hold one embedding per input")
    ordered: list[list[float] | None] = [None] * count
    for position, entry in enumerate(data):
        index = entry.get("index", position) if isinstance(entry, dict) else None
        vector = entry.get("embedding") if isinstance(entry, dict) else None
        if (not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < count
                or ordered[index] is not None or not isinstance(vector, list)
                or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                           for x in vector)):
            raise ValueError("the answer has an embedding that isn't a list of numbers")
        ordered[index] = [float(x) for x in vector]
    dimensions = {len(vector) for vector in ordered if vector is not None}
    if len(dimensions) != 1 or not 1 <= next(iter(dimensions)) <= MAX_EMBEDDING_DIMENSIONS:
        raise ValueError("the answer's embeddings aren't all of one usable dimension")
    return [vector for vector in ordered if vector is not None]


class GatewayService:
    """The gateway for one server: its pool, the server key that opens the endpoints' keys, and the count of
    calls running for each person under a hard budget."""

    def __init__(self, settings: OrgSettings, pool: Any) -> None:
        if settings.gateway_key is None:
            raise ValueError("the gateway needs ROW_BOT_ORG_GATEWAY_KEY")
        self.pool = pool
        self._server_key = settings.gateway_key
        self._budgeted_calls = settings.gateway_budgeted_calls
        self._running: dict[str, int] = {}
        self._running_lock = threading.Lock()

    def seal(self, endpoint_id: str, base_url: str, api_key: str) -> bytes:
        return seal(self._server_key, endpoint_id, base_url, api_key)

    # -- calls running under a hard budget -------------------------------------------------------

    def _take_slot(self, principal_id: str) -> bool:
        with self._running_lock:
            running = self._running.get(principal_id, 0)
            if running >= self._budgeted_calls:
                return False
            self._running[principal_id] = running + 1
            return True

    def _give_back_slot(self, principal_id: str) -> None:
        with self._running_lock:
            left = self._running.get(principal_id, 1) - 1
            if left > 0:
                self._running[principal_id] = left
            else:
                self._running.pop(principal_id, None)

    # -- the database, in worker threads ---------------------------------------------------------

    def _lookup(self, principal: RequestPrincipal, model: str) -> tuple[store.Route | None, list[store.BudgetState]]:
        with principal_transaction(self.pool, principal.principal_id, principal.groups) as conn:
            if not store.allowed(policy_store.effective(conn, principal.groups), model):
                return None, []
            return store.route(conn, model), store.budget_state(conn)

    def _record(self, principal: RequestPrincipal, route: store.Route, usage: upstream.Usage, started: float,
                outcome: str) -> None:
        cost = ((Decimal(usage.input_tokens) * route.input_price + Decimal(usage.output_tokens) * route.output_price)
                / _MILLION).quantize(_COST_PLACES)
        duration_ms = int((time.perf_counter() - started) * 1000)
        with principal_transaction(self.pool, principal.principal_id, principal.groups) as conn:
            store.record_usage(conn, principal_id=principal.principal_id, groups=principal.groups, model=route.model,
                               input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, cost=cost,
                               duration_ms=duration_ms, outcome=outcome)
            # The meter never depends on the audit event: an event that can't be built or stored is logged, and
            # the usage row stays (the answer already went out).
            try:
                with conn.transaction():
                    audit.emit(conn, Action.MODEL_CALLED, actor=audit.person(principal.principal_id),
                               outcome=Outcome.SUCCEEDED if outcome == "ok" else Outcome.FAILED,
                               device_id=principal.device_id,
                               details={"provider": route.api, "model": route.model,
                                        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                                        "duration_ms": duration_ms,
                                        "cost_micros": int((cost * _MILLION).to_integral_value())})
            except (InvalidAuditEvent, psycopg.Error) as error:
                logger.error("a call to endpoint %s was metered but not audited (%s)", route.endpoint_name,
                             type(error).__name__)

    async def record(self, principal: RequestPrincipal, route: store.Route, usage: upstream.Usage, started: float,
                     outcome: str) -> None:
        if (outcome == "ok" and usage.input_tokens == 0 and usage.output_tokens == 0
                and (route.input_price or route.output_price)):
            logger.warning("endpoint %s reported no usage for priced model %s", route.endpoint_name, route.model)
        # Shielded: a stream the caller dropped still records what it used.
        with anyio.CancelScope(shield=True):
            try:
                await run_in_threadpool(self._record, principal, route, usage, started, outcome)
            except Exception as error:  # noqa: BLE001 - the answer went out; the meter's failure is logged
                logger.error("usage for model %s wasn't recorded (%s)", route.model, type(error).__name__)

    # -- embeddings for the company brain, in process --------------------------------------------

    def embed(self, principal: RequestPrincipal, texts: list[str]) -> tuple[str, list[list[float]]]:
        """``texts`` embedded by the company's default embeddings model, as a call of ``principal``'s: metered to
        them and their groups, with its ``model.called``, and refused when one of their hard budgets is spent, like
        any call. Returns the catalog model's name and the vectors, in order. ``NoEmbeddingsModel`` when the catalog
        has no default embeddings model; ``EmbeddingsFailed`` for anything else. It waits for the endpoint: call it
        from a worker thread."""
        with principal_transaction(self.pool, principal.principal_id, principal.groups) as conn:
            route = store.default_route(conn, "embeddings")
            budgets = store.budget_state(conn) if route is not None else []
        if route is None or route.api != upstream.OPENAI:
            raise NoEmbeddingsModel
        try:
            if not route.enabled:
                raise _Refusal(503, "endpoint_disabled", "The embeddings model's endpoint is turned off.")
            _check_budgets(budgets)
            api_key = self._key_of(route)
        except _Refusal as refusal:
            raise EmbeddingsFailed(refusal.message) from None
        body = json.dumps({"model": route.upstream_model, "input": texts}).encode("utf-8")
        started = time.perf_counter()
        answer = bytearray()
        try:
            # No redirects, as for every call: one would carry the key elsewhere.
            with (httpx.Client(follow_redirects=False, timeout=httpx.Timeout(route.timeout_seconds)) as client,
                  client.stream("POST", upstream.target_url(route.api, route.base_url, "embeddings"),
                                headers=upstream.request_headers(route.api, api_key, {}), content=body) as response):
                status = response.status_code
                for chunk in response.iter_bytes():
                    answer += chunk
                    if len(answer) > MAX_RESPONSE_BYTES:
                        raise _Refusal(502, "endpoint_response_too_large", "The endpoint's answer was too large.")
        except (httpx.HTTPError, _Refusal) as error:
            self._meter(principal, route, upstream.Usage(), started, "error")
            raise EmbeddingsFailed(_upstream_refusal(route, error).message) from None
        if status >= 400:
            logger.warning("endpoint %s answered %s", route.endpoint_name, status)
            self._meter(principal, route, upstream.Usage(), started, "error")
            raise EmbeddingsFailed("The company's model endpoint refused the server's key." if status in (401, 403)
                                   else "The company's model endpoint couldn't embed the text.")
        try:
            parsed = json.loads(answer)
            vectors = _vectors(parsed, len(texts))
        except ValueError:
            logger.warning("endpoint %s gave embeddings that can't be used", route.endpoint_name)
            self._meter(principal, route, upstream.Usage(), started, "error")
            raise EmbeddingsFailed("The company's model endpoint gave an answer that can't be used.") from None
        self._meter(principal, route, upstream.usage_of(route.api, parsed), started, "ok")
        return route.model, vectors

    def _meter(self, principal: RequestPrincipal, route: store.Route, usage: upstream.Usage, started: float,
               outcome: str) -> None:
        try:
            self._record(principal, route, usage, started, outcome)
        except Exception as error:  # noqa: BLE001 - the vectors stand; the meter's failure is logged
            logger.error("usage for model %s wasn't recorded (%s)", route.model, type(error).__name__)

    # -- a call ----------------------------------------------------------------------------------

    async def call(self, request: Request, principal: RequestPrincipal, api: str, kind: str) -> Response:
        try:
            return await self._call(request, principal, api, kind)
        except _Refusal as refusal:
            return JSONResponse(upstream.error_body(api, refusal.code, refusal.message), status_code=refusal.status)

    async def _call(self, request: Request, principal: RequestPrincipal, api: str, kind: str) -> Response:
        payload = await _read_payload(request)
        model = payload.get("model")
        if not isinstance(model, str) or not model:
            raise _Refusal(400, "invalid_request", "The request needs a model.")
        passed = _passed_headers(request) if api == upstream.ANTHROPIC else {}
        if problem := upstream.request_problem(api, payload, passed):
            raise _Refusal(400, "invalid_request", problem)
        route, budgets = await run_in_threadpool(self._lookup, principal, model)
        if route is None:
            raise _Refusal(403, "model_not_allowed", "This model isn't available to you.")
        if route.api != api:
            raise _Refusal(400, "wrong_api", f"This model is called through the {route.api} gateway.")
        if route.kind != kind:
            raise _Refusal(400, "wrong_model_kind", f"This is a {route.kind} model.")
        if not route.enabled:
            raise _Refusal(503, "endpoint_disabled", "This model's endpoint is turned off.")
        warnings = _check_budgets(budgets)
        api_key = self._key_of(route)
        # Budgets count a call's spend after it, so a person under a hard budget gets only a few at once.
        slotted = any(budget.hard_limit is not None for budget in budgets)
        if slotted and not self._take_slot(principal.principal_id):
            raise _Refusal(429, "too_many_calls", "Too many model calls at once under a budget: wait for one to end.")
        try:
            return await self._forward(principal, route, payload, passed, warnings, slotted, api_key)
        except BaseException:
            if slotted:
                self._give_back_slot(principal.principal_id)
            raise

    async def _forward(self, principal: RequestPrincipal, route: store.Route, payload: dict[str, Any],
                       passed: dict[str, str], warnings: list[str], slotted: bool, api_key: str | None) -> Response:
        """Send the call; the slot (when ``slotted``) is given back when its answer is done, or by the caller
        when this raises."""
        api, kind = route.api, route.kind
        stream = kind == "chat" and payload.get("stream") is True
        payload["model"] = route.upstream_model
        if stream and api == upstream.OPENAI:
            options = payload.get("stream_options")
            payload["stream_options"] = {**(options if isinstance(options, dict) else {}), "include_usage": True}
        headers = {BUDGET_WARNING_HEADER: ", ".join(warnings)} if warnings else {}

        # A client per call, closed with its answer. No redirects: one would carry the key (x-api-key)
        # to wherever it points.
        client = httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(route.timeout_seconds))
        started = time.perf_counter()
        try:
            response = await client.send(
                client.build_request("POST", upstream.target_url(api, route.base_url, kind),
                                     headers=upstream.request_headers(api, api_key, passed),
                                     content=json.dumps(payload).encode("utf-8")),
                stream=True)
        except httpx.HTTPError as error:
            await _close(client)
            await self.record(principal, route, upstream.Usage(), started, "error")
            raise _upstream_refusal(route, error) from None

        if response.status_code >= 400:
            refusal = await self._failed(route, response, api_key)
            await _close(client, response)
            await self.record(principal, route, upstream.Usage(), started, "error")
            self._done(principal, slotted)
            return refusal
        if stream:
            relay = _Relay(self, principal, route, client, response, started, slotted)
            return _RelayResponse(relay, status_code=response.status_code, media_type="text/event-stream",
                                  headers=headers)
        try:
            body = await _read_capped(response)
            answer = json.loads(body)
            usage = upstream.usage_of(api, answer)
        except (httpx.HTTPError, ValueError, _Refusal) as error:
            await self.record(principal, route, upstream.Usage(), started, "error")
            raise _upstream_refusal(route, error) from None
        finally:
            await _close(client, response)
        if isinstance(answer, dict) and answer.get("model") != route.upstream_model:
            logger.warning("endpoint %s answered for model %s with another model", route.endpoint_name, route.model)
        await self.record(principal, route, usage, started, "ok")
        self._done(principal, slotted)
        return Response(body, status_code=response.status_code, media_type="application/json", headers=headers)

    def _done(self, principal: RequestPrincipal, slotted: bool) -> None:
        if slotted:
            self._give_back_slot(principal.principal_id)

    def _key_of(self, route: store.Route) -> str | None:
        if route.key_sealed is None:
            return None
        try:
            return unseal(self._server_key, route.endpoint_id, route.base_url, route.key_sealed)
        except KeyUnavailable:
            logger.error("the key of endpoint %s doesn't open with this server's gateway key", route.endpoint_name)
            raise _Refusal(503, "endpoint_key_unavailable", "This model's endpoint can't be used right now.") from None

    async def _failed(self, route: store.Route, response: httpx.Response, api_key: str | None) -> Response:
        """The endpoint's error in the caller's wire format: only a 4xx's type and message (without the key)
        pass on; a refused key is the server's problem, and a 5xx says only that the endpoint failed."""
        try:
            body = await _read_capped(response)
        except (httpx.HTTPError, _Refusal):
            body = b""
        status = response.status_code
        logger.warning("endpoint %s answered %s", route.endpoint_name, status)
        if status in (401, 403):
            return JSONResponse(upstream.error_body(route.api, "endpoint_auth_failed",
                                                    "The company's model endpoint refused the server's key."),
                                status_code=502)
        if status >= 500:
            return JSONResponse(upstream.error_body(route.api, "endpoint_error",
                                                    "The company's model endpoint failed."), status_code=502)
        kind, message = upstream.endpoint_error(body)
        if message and api_key:
            message = message.replace(api_key, "[redacted]")
        return JSONResponse(upstream.error_body(route.api, kind or "endpoint_rejected",
                                                message or "The company's model endpoint refused the request."),
                            status_code=status)


class _Relay:
    """A stream passed through line by line, its usage read on the way; ``finish`` runs once, however it ends."""

    def __init__(self, service: GatewayService, principal: RequestPrincipal, route: store.Route,
                 client: httpx.AsyncClient, response: httpx.Response, started: float, slotted: bool) -> None:
        self.service, self.principal, self.route = service, principal, route
        self.client, self.response, self.started, self.slotted = client, response, started, slotted
        self.meter = upstream.StreamUsage(route.api)
        self.outcome = "ok"
        self.finished = False

    async def lines(self) -> AsyncIterator[bytes]:
        try:
            async for line in self.response.aiter_lines():
                self.meter.feed(line)
                yield (line + "\n").encode("utf-8")
        except httpx.HTTPError as error:
            self.outcome = "error"
            refusal = _upstream_refusal(self.route, error)
            yield upstream.stream_error_event(self.route.api, refusal.code, refusal.message)
        finally:
            await self.finish()

    async def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        # A stream the caller dropped before its end is recorded as an error, with what it used so far.
        if self.outcome == "ok" and not self.response.is_stream_consumed:
            self.outcome = "error"
        try:
            await _close(self.client, self.response)
            await self.service.record(self.principal, self.route, self.meter.usage, self.started, self.outcome)
        finally:
            self.service._done(self.principal, self.slotted)


class _RelayResponse(StreamingResponse):
    """Finishes its relay even when the caller goes before the stream starts (its lines never run then)."""

    def __init__(self, relay: _Relay, **options: Any) -> None:
        super().__init__(relay.lines(), **options)
        self.relay = relay

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.relay.finish()


async def _close(client: httpx.AsyncClient, response: httpx.Response | None = None) -> None:
    with anyio.CancelScope(shield=True):
        if response is not None:
            await response.aclose()
        await client.aclose()


async def _read_payload(request: Request) -> dict[str, Any]:
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_REQUEST_BYTES:
            raise _Refusal(413, "request_too_large", "The request is larger than 16 MiB.")
    try:
        payload = json.loads(body)
    except ValueError:
        raise _Refusal(400, "invalid_request", "The request body isn't JSON.") from None
    if not isinstance(payload, dict):
        raise _Refusal(400, "invalid_request", "The request body must be a JSON object.")
    return payload


def _passed_headers(request: Request) -> dict[str, str]:
    passed = {}
    for name in upstream.ANTHROPIC_PASS_THROUGH:
        value = request.headers.get(name)
        if value is None:
            continue
        if not upstream.valid_header_value(value):
            raise _Refusal(400, "invalid_request", f"The {name} header isn't valid.")
        passed[name] = value
    return passed


async def _read_capped(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > MAX_RESPONSE_BYTES:
            raise _Refusal(502, "endpoint_response_too_large", "The endpoint's answer was too large.")
    return bytes(body)


def _check_budgets(budgets: list[store.BudgetState]) -> list[str]:
    """Refuse when a hard limit is spent; the soft limits reached, as warnings."""
    warnings: list[str] = []
    for budget in budgets:
        if budget.hard_limit is not None and budget.spent >= budget.hard_limit:
            whose = "Your" if budget.scope == "person" else f"The {budget.subject} group's"
            raise _Refusal(429, "budget_exceeded", f"{whose} monthly model budget is used up.")
        if budget.soft_limit is not None and budget.spent >= budget.soft_limit and budget.scope not in warnings:
            warnings.append(budget.scope)
    return warnings


def _upstream_refusal(route: store.Route, error: Exception) -> _Refusal:
    if isinstance(error, _Refusal):
        return error
    logger.warning("model call to endpoint %s failed (%s)", route.endpoint_name, type(error).__name__)
    if isinstance(error, httpx.TimeoutException):
        return _Refusal(504, "endpoint_timeout", "The company's model endpoint didn't answer in time.")
    if isinstance(error, ValueError):
        return _Refusal(502, "bad_endpoint_response", "The company's model endpoint gave an answer that isn't JSON.")
    return _Refusal(502, "endpoint_unreachable", "The company's model endpoint can't be reached.")


# -- the admin API's bodies ---------------------------------------------------------------------------

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
# The id people call a model by, as the policy's models.allowed names it (without its "*"), at most as long as
# an audit event's model id (row_bot.audit.vocabulary.MODEL_PATTERN).
CatalogName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=128,
                                               pattern=f"^(?:{CATALOG_MODEL_ID})$")]
Group = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
# Printable ASCII: it goes into an HTTP header.
ApiKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192, pattern=r"^[!-~]+$")]
Price = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=6)]


def _base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises on a bad port
    except ValueError:
        raise ValueError("must be an http:// or https:// address") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("must be an http:// or https:// address")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("must have no credentials, query or fragment (the key goes in api_key)")
    if len(value) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError("must be a plain address of at most 2048 characters")
    return value


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EndpointIn(_Body):
    name: Name
    api: Literal["openai", "anthropic"]
    base_url: str
    api_key: ApiKey | None = None
    timeout_seconds: int = Field(60, ge=1, le=600)
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        return _base_url(value)


class EndpointChange(_Body):
    name: Name | None = None
    base_url: str | None = None
    # A new key; null removes it.
    api_key: ApiKey | None = None
    timeout_seconds: int | None = Field(None, ge=1, le=600)
    enabled: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str | None) -> str | None:
        return None if value is None else _base_url(value)


class ModelIn(_Body):
    endpoint_id: uuid.UUID
    name: CatalogName
    upstream_model: ModelName | None = None
    display_name: ModelName | None = None
    kind: Literal["chat", "embeddings"]
    context_window: int | None = Field(None, ge=1)
    input_price: Price = Decimal(0)
    output_price: Price = Decimal(0)
    is_default: bool = False


class ModelChange(_Body):
    name: CatalogName | None = None
    upstream_model: ModelName | None = None
    display_name: ModelName | None = None
    context_window: int | None = Field(None, ge=1)
    input_price: Price | None = None
    output_price: Price | None = None
    is_default: bool | None = None


class BudgetIn(_Body):
    scope: Literal["person", "group"]
    # A principal's id (person) or a group's name.
    subject: Group
    soft_limit: Price | None = None
    hard_limit: Price | None = None


def _error(status: int, error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status)


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _set_fields(body: BaseModel, *, nullable: frozenset[str] = frozenset()) -> dict[str, Any]:
    """The fields the caller sent; null only where it means something (``nullable``)."""
    return {name: getattr(body, name) for name in body.model_fields_set
            if getattr(body, name) is not None or name in nullable}


def _month_start(day: date) -> datetime:
    return datetime(day.year, day.month, 1, tzinfo=timezone.utc)


def gateway_router(service: GatewayService) -> APIRouter:
    router = APIRouter()
    admin = Depends(admin_connection, scope="function")
    caller = Depends(request_principal)

    def changed(conn: psycopg.Connection, principal: RequestPrincipal, setting: str, change: str | None) -> None:
        audit.emit(conn, Action.ORG_SETTINGS_CHANGED, actor=audit.admin(principal.principal_id),
                   target=Target(TargetKind.SETTING, f"gateway.{setting}"),
                   details={} if change is None else {"change": change})

    # -- for connected Row-Bots ------------------------------------------------------------------

    @router.get("/api/models")
    def models(principal: RequestPrincipal = Depends(request_principal),
               conn: psycopg.Connection = Depends(principal_connection, scope="function")) -> JSONResponse:
        policy = policy_store.effective(conn, principal.groups)
        return JSONResponse({"models": [model for model in store.available_models(conn)
                                        if store.allowed(policy, model["id"])]})

    @router.post("/api/gateway/openai/v1/chat/completions")
    async def openai_chat(request: Request, principal: RequestPrincipal = Depends(request_principal)) -> Response:
        return await service.call(request, principal, upstream.OPENAI, "chat")

    @router.post("/api/gateway/openai/v1/embeddings")
    async def openai_embeddings(request: Request,
                                principal: RequestPrincipal = Depends(request_principal)) -> Response:
        return await service.call(request, principal, upstream.OPENAI, "embeddings")

    @router.post("/api/gateway/anthropic/v1/messages")
    async def anthropic_messages(request: Request,
                                 principal: RequestPrincipal = Depends(request_principal)) -> Response:
        return await service.call(request, principal, upstream.ANTHROPIC, "chat")

    # -- endpoints -------------------------------------------------------------------------------

    @router.get("/api/admin/endpoints")
    def list_endpoints(conn: psycopg.Connection = admin) -> JSONResponse:
        return JSONResponse({"endpoints": store.list_endpoints(conn)})

    @router.post("/api/admin/endpoints")
    def create_endpoint(body: EndpointIn, conn: psycopg.Connection = admin,
                        principal: RequestPrincipal = caller) -> JSONResponse:
        endpoint_id = str(uuid.uuid4())
        sealed = None if body.api_key is None else service.seal(endpoint_id, body.base_url, body.api_key)
        try:
            with conn.transaction():
                created = store.create_endpoint(conn, endpoint_id=endpoint_id, name=body.name, api=body.api,
                                                base_url=body.base_url, key_sealed=sealed,
                                                timeout_seconds=body.timeout_seconds, enabled=body.enabled)
        except psycopg.errors.UniqueViolation:
            return _error(409, "name_taken")
        changed(conn, principal, f"endpoint.{endpoint_id}", "created")
        return JSONResponse(created, status_code=201)

    @router.patch("/api/admin/endpoints/{endpoint_id}")
    def change_endpoint(endpoint_id: str, body: EndpointChange, conn: psycopg.Connection = admin,
                        principal: RequestPrincipal = caller) -> JSONResponse:
        endpoint_id = _uuid(endpoint_id) or ""
        current = store.endpoint_target(conn, endpoint_id) if endpoint_id else None
        if current is None:
            return _error(404, "endpoint_not_found")
        changes = _set_fields(body, nullable=frozenset({"api_key"}))
        base_url = changes.get("base_url", current[1])
        if "api_key" in changes:
            key = changes.pop("api_key")
            changes["key_sealed"] = None if key is None else service.seal(endpoint_id, base_url, key)
        elif endpoint_origin(base_url) != endpoint_origin(current[1]):
            # The key goes only to the host it was entered for: another host needs it entered again.
            changes["key_sealed"] = None
        try:
            with conn.transaction():
                updated = store.update_endpoint(conn, endpoint_id, changes)
        except psycopg.errors.UniqueViolation:
            return _error(409, "name_taken")
        if not updated:
            return _error(404, "endpoint_not_found")
        changed(conn, principal, f"endpoint.{endpoint_id}", "updated")
        return JSONResponse(updated)

    @router.delete("/api/admin/endpoints/{endpoint_id}")
    def delete_endpoint(endpoint_id: str, conn: psycopg.Connection = admin,
                        principal: RequestPrincipal = caller) -> Response:
        endpoint_id = _uuid(endpoint_id) or ""
        try:
            with conn.transaction():
                deleted = bool(endpoint_id) and store.delete_endpoint(conn, endpoint_id)
        except psycopg.errors.ForeignKeyViolation:
            return _error(409, "endpoint_has_models")
        if not deleted:
            return _error(404, "endpoint_not_found")
        changed(conn, principal, f"endpoint.{endpoint_id}", "deleted")
        return Response(status_code=204)

    # -- models ----------------------------------------------------------------------------------

    @router.get("/api/admin/models")
    def list_models(conn: psycopg.Connection = admin) -> JSONResponse:
        return JSONResponse({"models": store.list_models(conn)})

    @router.post("/api/admin/models")
    def create_model(body: ModelIn, conn: psycopg.Connection = admin,
                     principal: RequestPrincipal = caller) -> JSONResponse:
        target = store.endpoint_target(conn, str(body.endpoint_id))
        if target is None:
            return _error(404, "endpoint_not_found")
        if target[0] == upstream.ANTHROPIC and body.kind == "embeddings":
            return _error(400, "anthropic_has_no_embeddings")
        fields = body.model_dump()
        fields["endpoint_id"] = str(body.endpoint_id)
        fields["upstream_model"] = body.upstream_model or body.name
        fields["display_name"] = body.display_name or body.name
        try:
            with conn.transaction():
                created = store.create_model(conn, fields)
        except psycopg.errors.UniqueViolation:
            return _error(409, "name_taken")
        changed(conn, principal, f"model.{created['id']}", "created")
        return JSONResponse(created, status_code=201)

    @router.patch("/api/admin/models/{model_id}")
    def change_model(model_id: str, body: ModelChange, conn: psycopg.Connection = admin,
                     principal: RequestPrincipal = caller) -> JSONResponse:
        model_id = _uuid(model_id) or ""
        try:
            with conn.transaction():
                updated = store.update_model(conn, model_id, _set_fields(body, nullable=frozenset(
                    {"context_window"}))) if model_id else None
        except psycopg.errors.UniqueViolation:
            return _error(409, "name_taken")
        if not updated:
            return _error(404, "model_not_found")
        changed(conn, principal, f"model.{model_id}", "updated")
        return JSONResponse(updated)

    @router.delete("/api/admin/models/{model_id}")
    def delete_model(model_id: str, conn: psycopg.Connection = admin,
                     principal: RequestPrincipal = caller) -> Response:
        model_id = _uuid(model_id) or ""
        if not (model_id and store.delete_model(conn, model_id)):
            return _error(404, "model_not_found")
        changed(conn, principal, f"model.{model_id}", "deleted")
        return Response(status_code=204)

    # -- budgets and usage -----------------------------------------------------------------------

    @router.get("/api/admin/budgets")
    def list_budgets(conn: psycopg.Connection = admin) -> JSONResponse:
        return JSONResponse({"budgets": store.list_budgets(conn)})

    @router.put("/api/admin/budgets")
    def put_budget(body: BudgetIn, conn: psycopg.Connection = admin,
                   principal: RequestPrincipal = caller) -> JSONResponse:
        subject = _uuid(body.subject) if body.scope == "person" else body.subject
        if subject is None:
            return _error(400, "person_budget_needs_a_principal_id")
        if body.soft_limit is None and body.hard_limit is None:
            return _error(400, "budget_needs_a_limit")
        if body.soft_limit is not None and body.hard_limit is not None and body.soft_limit > body.hard_limit:
            return _error(400, "soft_limit_above_hard_limit")
        budget = store.put_budget(conn, scope=body.scope, subject=subject, soft_limit=body.soft_limit,
                                  hard_limit=body.hard_limit)
        changed(conn, principal, f"budget.{budget['id']}", None)  # Set: made or changed.
        return JSONResponse(budget)

    @router.delete("/api/admin/budgets/{budget_id}")
    def delete_budget(budget_id: str, conn: psycopg.Connection = admin,
                      principal: RequestPrincipal = caller) -> Response:
        budget_id = _uuid(budget_id) or ""
        if not (budget_id and store.delete_budget(conn, budget_id)):
            return _error(404, "budget_not_found")
        changed(conn, principal, f"budget.{budget_id}", "deleted")
        return Response(status_code=204)

    @router.get("/api/admin/usage")
    def usage(by: Literal["person", "group", "model"] = "person", start: date | None = None,
              end: date | None = Query(None, description="exclusive"),
              conn: psycopg.Connection = admin) -> JSONResponse:
        today = datetime.now(timezone.utc).date()
        since = datetime(start.year, start.month, start.day, tzinfo=timezone.utc) if start else _month_start(today)
        if end is not None:
            until = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
        else:
            until = _month_start(date(since.year + since.month // 12, since.month % 12 + 1, 1))
        if until <= since:
            return _error(400, "end_before_start")
        return JSONResponse({"by": by, "start": since.isoformat(), "end": until.isoformat(),
                             "usage": store.usage_report(conn, by=by, start=since, end=until)})

    return router


__all__ = ["BUDGET_WARNING_HEADER", "MAX_EMBEDDING_DIMENSIONS", "EmbeddingsFailed", "GatewayService",
           "NoEmbeddingsModel", "gateway_router"]
