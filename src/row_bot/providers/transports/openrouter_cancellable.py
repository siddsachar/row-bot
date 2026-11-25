"""Cancellable LangChain OpenRouter wrapper.

For ``anthropic/claude-*`` models it also caches a conversation's tail with
OpenRouter's documented top-level ``cache_control`` (with ``ttl``), and retries
a request once without cache instructions when OpenRouter rejects them
(``prompt_cache_breakpoints``). A failed request is logged with OpenRouter's
own details, and a busy free model fails in plain words (``openrouter_errors``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openrouter import ChatOpenRouter

from row_bot.brand import APP_DISPLAY_NAME, APP_WEBSITE_URL
from row_bot.cancellation import current_cancellation_scope
from row_bot.prompt_cache import content_has_cache_control, conversation_cache_ttl
from row_bot.providers.cache_affinity import conversation_cache_key
from row_bot.providers.capabilities import PROMPT_CACHE_ROUTE_OPENROUTER, prompt_cache_route
from row_bot.providers.transports import prompt_cache_breakpoints as cache_breakpoints
from row_bot.providers.transports.openrouter_errors import described_failure
from row_bot.providers.transports.cancellable_http import (
    cancellable_async_http_client,
    cancellable_http_client,
)

# OpenRouter credits usage, in its public app rankings, to the app these name:
# the SDK sends them as the HTTP-Referer and X-OpenRouter-Title headers on every
# request. They identify Row-Bot, never the user, the account or the request.
# Every OpenRouter SDK client Row-Bot builds passes them; ChatOpenRouter's own
# app_url/app_title defaults would credit LangChain instead.
OPENROUTER_APP_ATTRIBUTION = {"http_referer": APP_WEBSITE_URL, "x_open_router_title": APP_DISPLAY_NAME}


class CancellableChatOpenRouter(ChatOpenRouter):
    """``ChatOpenRouter`` with caller-owned cancellable OpenRouter SDK clients.

    Each request of a conversation carries the conversation's ``session_id``,
    OpenRouter's sticky-routing key: the conversation stays on the upstream
    provider that holds its prompt cache. ``provider.order`` is never set,
    because it turns stickiness off. Calls outside a conversation send none.
    """

    @property
    def _default_params(self) -> dict[str, Any]:
        params = super()._default_params
        if not params.get("session_id"):
            session_id = conversation_cache_key("openrouter")
            if session_id:
                params["session_id"] = session_id
        return params

    # ── Prompt caching (Claude models) ──────────────────────────────────────

    def _cache_route(self) -> tuple[str, str]:
        return ("openrouter", str(self.model_name or ""))

    def _sends_cache_markers(self, messages: list[BaseMessage]) -> bool:
        if prompt_cache_route("openrouter", self.model_name) != PROMPT_CACHE_ROUTE_OPENROUTER:
            return False
        if cache_breakpoints.cache_markers_rejected(self._cache_route()):
            return False
        return bool(conversation_cache_ttl(messages)) or any(
            isinstance(message, SystemMessage) and content_has_cache_control(message.content)
            for message in messages
        )

    def _create_message_dicts(
        self, messages: list[BaseMessage], stop: list[str] | None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        message_dicts, params = super()._create_message_dicts(messages, stop)
        if prompt_cache_route("openrouter", self.model_name) != PROMPT_CACHE_ROUTE_OPENROUTER:
            return message_dicts, params
        if cache_breakpoints.cache_markers_rejected(self._cache_route()):
            # This route rejected cache instructions earlier in this process.
            return cache_breakpoints.without_cache_markers({"messages": message_dicts})["messages"], params
        tail_ttl = conversation_cache_ttl(messages)
        if tail_ttl:
            params = cache_breakpoints.with_openrouter_tail(params, message_dicts, ttl=tail_ttl)
        return message_dicts, params

    def _cache_retry(self, messages: list[BaseMessage], exc: BaseException) -> bool:
        """Whether to retry once without cache instructions (the route is remembered)."""
        if not self._sends_cache_markers(messages) or not cache_breakpoints.is_cache_control_rejection(exc):
            return False
        scope = current_cancellation_scope()
        if scope is not None and scope.is_cancelled():
            return False
        cache_breakpoints.remember_cache_markers_rejected(self._cache_route())
        cache_breakpoints.log_cache_retry(self._cache_route(), remembered=True)
        return True

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        with self._described_errors():
            try:
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as exc:
                if not self._cache_retry(messages, exc):
                    raise
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        with self._described_errors():
            try:
                return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as exc:
                if not self._cache_retry(messages, exc):
                    raise
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        started = False
        with self._described_errors():
            try:
                for chunk in super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    started = True
                    yield chunk
                return
            except Exception as exc:
                # Only a rejection before anything streamed: nothing has reached the caller yet.
                if started or not self._cache_retry(messages, exc):
                    raise
            yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        started = False
        with self._described_errors():
            try:
                async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    started = True
                    yield chunk
                return
            except Exception as exc:
                if started or not self._cache_retry(messages, exc):
                    raise
            async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                yield chunk

    @contextmanager
    def _described_errors(self) -> Iterator[None]:
        """Log a failed request with OpenRouter's details; a busy free model fails in plain words."""
        try:
            yield
        except Exception as exc:
            busy = described_failure(exc, str(self.model_name or ""))
            if busy is not None:
                raise busy from exc
            raise

    # ── Transport ───────────────────────────────────────────────────────────

    def _build_client(self) -> Any:
        import openrouter
        from openrouter.utils import BackoffStrategy, RetryConfig

        client_kwargs: dict[str, Any] = {
            "api_key": self.openrouter_api_key.get_secret_value(),  # type: ignore[union-attr]
            **OPENROUTER_APP_ATTRIBUTION,
        }
        if self.openrouter_api_base:
            client_kwargs["server_url"] = self.openrouter_api_base

        client_kwargs["client"] = cancellable_http_client(follow_redirects=True)
        client_kwargs["async_client"] = cancellable_async_http_client(follow_redirects=True)

        if self.request_timeout is not None:
            client_kwargs["timeout_ms"] = self.request_timeout
        if self.max_retries > 0:
            client_kwargs["retry_config"] = RetryConfig(
                strategy="backoff",
                backoff=BackoffStrategy(
                    initial_interval=500,
                    max_interval=60000,
                    exponent=1.5,
                    max_elapsed_time=self.max_retries * 150_000,
                ),
                retry_connection_errors=True,
            )
        return openrouter.OpenRouter(**client_kwargs)
