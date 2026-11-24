"""Cancellable LangChain Anthropic wrapper.

``langchain-anthropic`` does not expose ``http_client`` as a
``ChatAnthropic`` constructor field in Row-Bot's locked version. Passing that
kwarg is treated as a model request kwarg, not as transport configuration. This
subclass keeps LangChain's Anthropic message/tool handling while constructing
the underlying Anthropic SDK clients with Row-Bot cancellable httpx clients.

On the direct Anthropic API (``thinking_binding=True``) it also applies the
preserved-thinking controls and the one strip-and-retry recovery described in
``thinking_binding``. With ``prompt_cache=True`` (direct API only) it caches a
conversation's tail with top-level automatic caching, adds one breakpoint for
long appends, and retries a request once without cache instructions when the
API rejects them (``prompt_cache_breakpoints``).
"""

from __future__ import annotations

import logging
import os
from functools import cached_property
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessageChunk

from row_bot.cancellation import current_cancellation_scope
from row_bot.prompt_cache import conversation_cache_ttl
from row_bot.providers.transports import prompt_cache_breakpoints as cache_breakpoints
from row_bot.providers.transports.cancellable_http import (
    RequestStopped,
    cancellable_async_http_client,
    cancellable_http_client,
)
from row_bot.providers.transports.thinking_binding import (
    log_input_transformations,
    remember_thinking_binding_rejected,
    thinking_binding_rejected,
    thinking_recovery_payload,
    with_thinking_binding,
)

logger = logging.getLogger(__name__)


class AnthropicRequestStopped(RequestStopped, anthropic.AnthropicError):
    """Stop ended the request. The Anthropic SDK passes its own errors on without retrying them."""


def _http_client_kwargs(client_params: dict[str, Any], anthropic_proxy: str | None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "base_url": client_params.get("base_url")
        or os.environ.get("ANTHROPIC_BASE_URL")
        or "https://api.anthropic.com",
    }
    if "timeout" in client_params:
        kwargs["timeout"] = client_params["timeout"]
    if anthropic_proxy:
        kwargs["proxy"] = anthropic_proxy
    return kwargs


class CancellableChatAnthropic(ChatAnthropic):
    """``ChatAnthropic`` whose requests Stop ends at once (``cancellable_http``)."""

    thinking_binding: bool = False
    """Apply preserved-thinking compliance (see ``thinking_binding``).

    Only the direct Anthropic provider sets this. Anthropic-compatible gateways
    (MiniMax, OpenCode) leave it off: the beta is unverified there.
    """

    prompt_cache: bool = False
    """Cache the tail of conversation requests (see ``prompt_cache_breakpoints``).

    Only the direct Anthropic provider sets this. MiniMax and OpenCode leave it
    off until a live check shows they accept cache instructions.
    """

    def _request_hooks(self, *, asynchronous: bool) -> list[Any]:
        """httpx request hooks for the SDK clients; none unless a gateway needs them."""
        return []

    def _get_request_payload(self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if self.thinking_binding and not thinking_binding_rejected(self._thinking_binding_route()):
            payload = with_thinking_binding(payload)
        if self.prompt_cache:
            tail_ttl = conversation_cache_ttl(self._convert_input(input_).to_messages())
            if tail_ttl:
                payload = cache_breakpoints.place_anthropic_breakpoints(payload, ttl=tail_ttl)
        return payload

    def _create(self, payload: dict) -> Any:
        try:
            return super()._create(payload)
        except anthropic.BadRequestError as exc:
            retry = self._thinking_recovery(payload, exc) or self._cache_recovery(payload, exc)
            if retry is None:
                raise
        return super()._create(retry)

    async def _acreate(self, payload: dict) -> Any:
        try:
            return await super()._acreate(payload)
        except anthropic.BadRequestError as exc:
            retry = self._thinking_recovery(payload, exc) or self._cache_recovery(payload, exc)
            if retry is None:
                raise
        return await super()._acreate(retry)

    def _cache_recovery(self, payload: dict, exc: anthropic.BadRequestError) -> dict | None:
        """The one retry without cache instructions, for a 400 that names them.

        The direct API documents every instruction Row-Bot sends, so a rejection
        means this request was malformed: it is retried, and the route is not
        remembered. Thinking-binding 400s are handled first, by ``_thinking_recovery``.
        """
        if not self.prompt_cache or not cache_breakpoints.has_cache_markers(payload):
            return None
        scope = current_cancellation_scope()
        if scope is not None and scope.is_cancelled():
            return None
        if not cache_breakpoints.is_cache_control_rejection(exc):
            return None
        cache_breakpoints.log_cache_retry(("anthropic", str(self.model or "")), remembered=False)
        return cache_breakpoints.without_cache_markers(payload)

    def _thinking_recovery(self, payload: dict, exc: anthropic.BadRequestError) -> dict | None:
        """The single strip-and-retry payload for a thinking-binding 400, if any.

        This runs inside the model call, so it comes before
        ``ReasoningFallbackChatModel``, which never retries these errors itself.
        """
        if not self.thinking_binding:
            return None
        scope = current_cancellation_scope()
        if scope is not None and scope.is_cancelled():
            return None
        recovery = thinking_recovery_payload(payload, exc)
        if recovery is None:
            return None
        retry, stripped, controls_rejected = recovery
        if controls_rejected:
            remember_thinking_binding_rejected(self._thinking_binding_route())
            logger.warning(
                "anthropic_thinking_binding: endpoint rejected the binding controls; "
                "retrying once without them and without %d thinking block(s) model=%s",
                stripped,
                self.model,
            )
        else:
            logger.warning(
                "anthropic_thinking_binding: thinking signature rejected; "
                "retrying once without %d thinking block(s) model=%s",
                stripped,
                self.model,
            )
        return retry

    def _thinking_binding_route(self) -> tuple[str, str]:
        return (str(self.anthropic_api_url or ""), str(self.model or ""))

    def _make_message_chunk_from_anthropic_event(
        self,
        event: Any,
        *,
        stream_usage: bool = True,
        coerce_content_to_string: bool,
        block_start_event: Any = None,
    ) -> tuple[AIMessageChunk | None, Any]:
        chunk, block_start_event = super()._make_message_chunk_from_anthropic_event(
            event,
            stream_usage=stream_usage,
            coerce_content_to_string=coerce_content_to_string,
            block_start_event=block_start_event,
        )
        if self.thinking_binding:
            transformations = _event_input_transformations(event)
            # langchain-anthropic drops this field from streams; log it and,
            # when it reports something, keep it on the chunk's metadata.
            if log_input_transformations(transformations, model=self.model) and chunk is not None:
                chunk.response_metadata["input_transformations"] = list(transformations)
        return chunk, block_start_event

    def _format_output(self, data: Any, **kwargs: Any) -> Any:
        result = super()._format_output(data, **kwargs)
        if self.thinking_binding:
            # Non-streaming results already carry the field in response_metadata.
            log_input_transformations(getattr(data, "input_transformations", None), model=self.model)
        return result

    @cached_property
    def _client(self) -> anthropic.Client:
        client_params = self._client_params
        return anthropic.Client(
            **client_params,
            http_client=cancellable_http_client(
                stopped=AnthropicRequestStopped,
                event_hooks={"request": self._request_hooks(asynchronous=False)},
                **_http_client_kwargs(client_params, self.anthropic_proxy),
            ),
        )

    @cached_property
    def _async_client(self) -> anthropic.AsyncClient:
        client_params = self._client_params
        return anthropic.AsyncClient(
            **client_params,
            http_client=cancellable_async_http_client(
                stopped=AnthropicRequestStopped,
                event_hooks={"request": self._request_hooks(asynchronous=True)},
                **_http_client_kwargs(client_params, self.anthropic_proxy),
            ),
        )


class CustomEndpointChatAnthropic(CancellableChatAnthropic):
    """Anthropic Messages at a custom endpoint, with the endpoint's connection check (H16) on every request."""

    endpoint: dict[str, Any] = {}

    def _request_hooks(self, *, asynchronous: bool) -> list[Any]:
        from row_bot.providers.endpoint_addresses import connection_check

        check = connection_check(self.endpoint)
        return check.event_hooks(asynchronous=asynchronous)["request"] if check is not None else []


class OpenCodeChatAnthropic(CancellableChatAnthropic):
    """Anthropic Messages through OpenCode, which routes each request on its headers."""

    def _request_hooks(self, *, asynchronous: bool) -> list[Any]:
        from row_bot.providers.opencode import add_opencode_request_headers, add_opencode_request_headers_async

        return [add_opencode_request_headers_async if asynchronous else add_opencode_request_headers]


def _event_input_transformations(event: Any) -> Any:
    """``input_transformations`` from a stream event: on ``message_start``'s message,
    and again on the final ``message_delta`` after a mid-stream server-side fallback."""
    event_type = getattr(event, "type", "")
    if event_type == "message_start":
        return getattr(getattr(event, "message", None), "input_transformations", None)
    if event_type == "message_delta":
        return getattr(event, "input_transformations", None)
    return None
