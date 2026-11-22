"""The gateway's routes.

For connected Row-Bots (any signed-in principal):

- ``GET /api/models``: the models this person may use;
- ``POST /api/gateway/openai/v1/chat/completions`` and ``.../embeddings``: OpenAI wire format;
- ``POST /api/gateway/anthropic/v1/messages``: Anthropic wire format.

A call is refused (in the caller's wire format) when the model isn't one the person may use, belongs
to the other API, is the wrong kind, sits on a disabled endpoint, or when the person's or one of
their groups' hard monthly budget is spent. A soft budget reached adds ``X-Row-Bot-Budget-Warning``
(``person``, ``group`` or both). The server sends the endpoint's key, passes streams through as they
come, and records one usage row per call it forwards, from the endpoint's own usage fields.

For admins (``row_bot_org.admin``): ``/api/admin/endpoints``, ``/api/admin/models``,
``/api/admin/budgets`` and ``GET /api/admin/usage``. An endpoint's key goes in once and never comes
back out: responses say only ``has_key``.

Never logged: request or response bodies, keys, the endpoint's error text. Logged: the endpoint's name
and the failure's kind.
"""

from __future__ import annotations

import json
import logging
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

from row_bot_org.admin import admin_connection
from row_bot_org.db.rls import principal_transaction
from row_bot_org.gateway import store, upstream
from row_bot_org.gateway.keys import KeyUnavailable, seal, unseal
from row_bot_org.identity.gate import RequestPrincipal, request_principal
from row_bot_org.identity.web import principal_connection
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


class GatewayService:
    """The gateway for one server: its pool and the server key that opens the endpoints' keys."""

    def __init__(self, settings: OrgSettings, pool: Any) -> None:
        if settings.gateway_key is None:
            raise ValueError("the gateway needs ROW_BOT_ORG_GATEWAY_KEY")
        self.pool = pool
        self._server_key = settings.gateway_key

    def seal(self, endpoint_id: str, api_key: str) -> bytes:
        return seal(self._server_key, endpoint_id, api_key)

    # -- the database, in worker threads ---------------------------------------------------------

    def _lookup(self, principal: RequestPrincipal, model: str) -> tuple[store.Route | None, list[store.BudgetState]]:
        with principal_transaction(self.pool, principal.principal_id, principal.groups) as conn:
            return store.route(conn, model), store.budget_state(conn)

    def _record(self, principal: RequestPrincipal, route: store.Route, usage: upstream.Usage, started: float,
                outcome: str) -> None:
        cost = ((Decimal(usage.input_tokens) * route.input_price + Decimal(usage.output_tokens) * route.output_price)
                / _MILLION).quantize(_COST_PLACES)
        with principal_transaction(self.pool, principal.principal_id, principal.groups) as conn:
            store.record_usage(conn, principal_id=principal.principal_id, groups=principal.groups, model=route.model,
                               input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, cost=cost,
                               duration_ms=int((time.perf_counter() - started) * 1000), outcome=outcome)

    async def record(self, principal: RequestPrincipal, route: store.Route, usage: upstream.Usage, started: float,
                     outcome: str) -> None:
        # Shielded: a stream the caller dropped still records what it used.
        with anyio.CancelScope(shield=True):
            try:
                await run_in_threadpool(self._record, principal, route, usage, started, outcome)
            except Exception as error:  # noqa: BLE001 - the answer went out; the meter's failure is logged
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
            return refusal
        if stream:
            return StreamingResponse(self._relay(principal, route, client, response, started),
                                     status_code=response.status_code, media_type="text/event-stream",
                                     headers=headers)
        try:
            body = await _read_capped(response)
            usage = upstream.usage_of(api, json.loads(body))
        except (httpx.HTTPError, ValueError, _Refusal) as error:
            await self.record(principal, route, upstream.Usage(), started, "error")
            raise _upstream_refusal(route, error) from None
        finally:
            await _close(client, response)
        await self.record(principal, route, usage, started, "ok")
        return Response(body, status_code=response.status_code, media_type="application/json", headers=headers)

    def _key_of(self, route: store.Route) -> str | None:
        if route.key_sealed is None:
            return None
        try:
            return unseal(self._server_key, route.endpoint_id, route.key_sealed)
        except KeyUnavailable:
            logger.error("the key of endpoint %s doesn't open with this server's gateway key", route.endpoint_name)
            raise _Refusal(503, "endpoint_key_unavailable", "This model's endpoint can't be used right now.") from None

    async def _failed(self, route: store.Route, response: httpx.Response, api_key: str | None) -> Response:
        """The endpoint's error, passed on without its key; a refused key is the server's problem, not the caller's."""
        try:
            body = await _read_capped(response)
        except (httpx.HTTPError, _Refusal):
            body = b""
        logger.warning("endpoint %s answered %s", route.endpoint_name, response.status_code)
        if response.status_code in (401, 403):
            return JSONResponse(upstream.error_body(route.api, "endpoint_auth_failed",
                                                    "The company's model endpoint refused the server's key."),
                                status_code=502)
        if api_key:
            body = body.replace(api_key.encode("utf-8"), b"[redacted]")
        status = response.status_code if response.status_code < 500 else 502
        return Response(body, status_code=status, media_type=response.headers.get("content-type", "application/json"))

    async def _relay(self, principal: RequestPrincipal, route: store.Route, client: httpx.AsyncClient,
                     response: httpx.Response, started: float) -> AsyncIterator[bytes]:
        meter = upstream.StreamUsage(route.api)
        outcome = "ok"
        try:
            async for line in response.aiter_lines():
                meter.feed(line)
                yield (line + "\n").encode("utf-8")
        except httpx.HTTPError as error:
            outcome = "error"
            refusal = _upstream_refusal(route, error)
            yield upstream.stream_error_event(route.api, refusal.code, refusal.message)
        finally:
            # A stream the caller dropped before its end is recorded as an error, with what it used so far.
            if outcome == "ok" and not response.is_stream_consumed:
                outcome = "error"
            await _close(client, response)
            await self.record(principal, route, meter.usage, started, outcome)


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
Group = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
ApiKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)]
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
    name: ModelName
    upstream_model: ModelName | None = None
    display_name: ModelName | None = None
    kind: Literal["chat", "embeddings"]
    context_window: int | None = Field(None, ge=1)
    input_price: Price = Decimal(0)
    output_price: Price = Decimal(0)
    allowed_groups: list[Group] = Field(default_factory=list, max_length=100)
    is_default: bool = False


class ModelChange(_Body):
    name: ModelName | None = None
    upstream_model: ModelName | None = None
    display_name: ModelName | None = None
    context_window: int | None = Field(None, ge=1)
    input_price: Price | None = None
    output_price: Price | None = None
    allowed_groups: list[Group] | None = Field(None, max_length=100)
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

    # -- for connected Row-Bots ------------------------------------------------------------------

    @router.get("/api/models")
    def models(conn: psycopg.Connection = Depends(principal_connection, scope="function")) -> JSONResponse:
        return JSONResponse({"models": store.available_models(conn)})

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
    def create_endpoint(body: EndpointIn, conn: psycopg.Connection = admin) -> JSONResponse:
        endpoint_id = str(uuid.uuid4())
        sealed = None if body.api_key is None else service.seal(endpoint_id, body.api_key)
        try:
            with conn.transaction():
                created = store.create_endpoint(conn, endpoint_id=endpoint_id, name=body.name, api=body.api,
                                                base_url=body.base_url, key_sealed=sealed,
                                                timeout_seconds=body.timeout_seconds, enabled=body.enabled)
        except psycopg.errors.UniqueViolation:
            return _error(409, "name_taken")
        return JSONResponse(created, status_code=201)

    @router.patch("/api/admin/endpoints/{endpoint_id}")
    def change_endpoint(endpoint_id: str, body: EndpointChange, conn: psycopg.Connection = admin) -> JSONResponse:
        endpoint_id = _uuid(endpoint_id) or ""
        changes = _set_fields(body, nullable=frozenset({"api_key"}))
        if "api_key" in changes:
            key = changes.pop("api_key")
            changes["key_sealed"] = None if key is None else service.seal(endpoint_id, key)
        try:
            with conn.transaction():
                changed = store.update_endpoint(conn, endpoint_id, changes) if endpoint_id else None
        except psycopg.errors.UniqueViolation:
            return _error(409, "name_taken")
        return JSONResponse(changed) if changed else _error(404, "endpoint_not_found")

    @router.delete("/api/admin/endpoints/{endpoint_id}")
    def delete_endpoint(endpoint_id: str, conn: psycopg.Connection = admin) -> Response:
        endpoint_id = _uuid(endpoint_id) or ""
        try:
            with conn.transaction():
                deleted = bool(endpoint_id) and store.delete_endpoint(conn, endpoint_id)
        except psycopg.errors.ForeignKeyViolation:
            return _error(409, "endpoint_has_models")
        return Response(status_code=204) if deleted else _error(404, "endpoint_not_found")

    # -- models ----------------------------------------------------------------------------------

    @router.get("/api/admin/models")
    def list_models(conn: psycopg.Connection = admin) -> JSONResponse:
        return JSONResponse({"models": store.list_models(conn)})

    @router.post("/api/admin/models")
    def create_model(body: ModelIn, conn: psycopg.Connection = admin) -> JSONResponse:
        api = store.endpoint_api(conn, str(body.endpoint_id))
        if api is None:
            return _error(404, "endpoint_not_found")
        if api == upstream.ANTHROPIC and body.kind == "embeddings":
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
        return JSONResponse(created, status_code=201)

    @router.patch("/api/admin/models/{model_id}")
    def change_model(model_id: str, body: ModelChange, conn: psycopg.Connection = admin) -> JSONResponse:
        model_id = _uuid(model_id) or ""
        try:
            with conn.transaction():
                changed = store.update_model(conn, model_id, _set_fields(body, nullable=frozenset(
                    {"context_window"}))) if model_id else None
        except psycopg.errors.UniqueViolation:
            return _error(409, "name_taken")
        return JSONResponse(changed) if changed else _error(404, "model_not_found")

    @router.delete("/api/admin/models/{model_id}")
    def delete_model(model_id: str, conn: psycopg.Connection = admin) -> Response:
        model_id = _uuid(model_id) or ""
        deleted = bool(model_id) and store.delete_model(conn, model_id)
        return Response(status_code=204) if deleted else _error(404, "model_not_found")

    # -- budgets and usage -----------------------------------------------------------------------

    @router.get("/api/admin/budgets")
    def list_budgets(conn: psycopg.Connection = admin) -> JSONResponse:
        return JSONResponse({"budgets": store.list_budgets(conn)})

    @router.put("/api/admin/budgets")
    def put_budget(body: BudgetIn, conn: psycopg.Connection = admin) -> JSONResponse:
        subject = _uuid(body.subject) if body.scope == "person" else body.subject
        if subject is None:
            return _error(400, "person_budget_needs_a_principal_id")
        if body.soft_limit is None and body.hard_limit is None:
            return _error(400, "budget_needs_a_limit")
        if body.soft_limit is not None and body.hard_limit is not None and body.soft_limit > body.hard_limit:
            return _error(400, "soft_limit_above_hard_limit")
        return JSONResponse(store.put_budget(conn, scope=body.scope, subject=subject, soft_limit=body.soft_limit,
                                             hard_limit=body.hard_limit))

    @router.delete("/api/admin/budgets/{budget_id}")
    def delete_budget(budget_id: str, conn: psycopg.Connection = admin) -> Response:
        budget_id = _uuid(budget_id) or ""
        deleted = bool(budget_id) and store.delete_budget(conn, budget_id)
        return Response(status_code=204) if deleted else _error(404, "budget_not_found")

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


__all__ = ["BUDGET_WARNING_HEADER", "GatewayService", "gateway_router"]
