from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import Any, AsyncIterator, Iterator, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.runnables.config import ensure_config, var_child_runnable_config

from row_bot.cancellation import current_cancellation_scope
from row_bot.providers.reasoning import (
    ReasoningRequestPlan,
    forget_reasoning_choice,
    queue_reasoning_notice,
    remember_background_rejection,
    suppress_reasoning_override,
)
from row_bot.providers.transports.responses_reasoning import is_encrypted_reasoning_rejection
from row_bot.providers.transports.thinking_binding import is_thinking_binding_error

logger = logging.getLogger(__name__)

_REASONING_ERROR_TERMS = ("reasoning", "thinking", "effort", "budget")


def _delegated_model_config() -> dict[str, Any]:
    """Keep inner model callbacks from duplicating the wrapper's stream."""
    config = dict(ensure_config())
    config["callbacks"] = []
    config.pop("run_id", None)
    config.pop("run_name", None)
    return config


@contextmanager
def _isolated_delegated_model_config() -> Iterator[dict[str, Any]]:
    """Override inherited callbacks while a bound inner runnable executes."""
    config = _delegated_model_config()
    token = var_child_runnable_config.set(config)
    try:
        yield config
    finally:
        var_child_runnable_config.reset(token)


class ReasoningFallbackChatModel(BaseChatModel):
    """Retry one rejected explicit reasoning request with Provider default.

    Thinking-binding 400s (``thinking_binding``) are not reasoning errors. The
    direct Anthropic transport recovers from them first, inside the primary
    call, and this wrapper never retries them.
    """

    primary: Any
    provider_default: Any
    plan: ReasoningRequestPlan
    provider_id: str

    @property
    def _llm_type(self) -> str:
        return "reasoning_fallback"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any],
        *,
        tool_choice: Any | None = None,
        **kwargs: Any,
    ) -> Runnable:
        return ReasoningFallbackChatModel(
            primary=_bind_tools(self.primary, tools, tool_choice=tool_choice, **kwargs),
            provider_default=_bind_tools(self.provider_default, tools, tool_choice=tool_choice, **kwargs),
            plan=self.plan,
            provider_id=self.provider_id,
            # The wrapper's own callbacks (the usage ledger) see each call once.
            callbacks=self.callbacks,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            with _isolated_delegated_model_config() as delegated_config:
                message = self.primary.invoke(
                    messages,
                    config=delegated_config,
                    stop=stop,
                    **kwargs,
                )
        except Exception as exc:
            if not should_retry_reasoning_error(exc):
                raise
            self._prepare_fallback(exc)
            with _isolated_delegated_model_config() as delegated_config:
                message = self.provider_default.invoke(
                    messages,
                    config=delegated_config,
                    stop=stop,
                    **kwargs,
                )
            self._fallback_succeeded()
        return _chat_result(message)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        emitted = False
        try:
            with _isolated_delegated_model_config() as delegated_config:
                for message in self.primary.stream(
                    messages,
                    config=delegated_config,
                    stop=stop,
                    **kwargs,
                ):
                    emitted = True
                    yield _generation_chunk(message)
            return
        except Exception as exc:
            if emitted or not should_retry_reasoning_error(exc):
                raise
            self._prepare_fallback(exc)
        with _isolated_delegated_model_config() as delegated_config:
            for message in self.provider_default.stream(
                messages,
                config=delegated_config,
                stop=stop,
                **kwargs,
            ):
                yield _generation_chunk(message)
        self._fallback_succeeded()

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            with _isolated_delegated_model_config() as delegated_config:
                message = await self.primary.ainvoke(
                    messages,
                    config=delegated_config,
                    stop=stop,
                    **kwargs,
                )
        except Exception as exc:
            if not should_retry_reasoning_error(exc):
                raise
            self._prepare_fallback(exc)
            with _isolated_delegated_model_config() as delegated_config:
                message = await self.provider_default.ainvoke(
                    messages,
                    config=delegated_config,
                    stop=stop,
                    **kwargs,
                )
            self._fallback_succeeded()
        return _chat_result(message)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        emitted = False
        try:
            with _isolated_delegated_model_config() as delegated_config:
                async for message in self.primary.astream(
                    messages,
                    config=delegated_config,
                    stop=stop,
                    **kwargs,
                ):
                    emitted = True
                    yield _generation_chunk(message)
            return
        except Exception as exc:
            if emitted or not should_retry_reasoning_error(exc):
                raise
            self._prepare_fallback(exc)
        with _isolated_delegated_model_config() as delegated_config:
            async for message in self.provider_default.astream(
                messages,
                config=delegated_config,
                stop=stop,
                **kwargs,
            ):
                yield _generation_chunk(message)
        self._fallback_succeeded()

    def _prepare_fallback(self, exc: BaseException) -> None:
        if self.plan.is_background:
            # A background call lowered reasoning on its own: the conversation's
            # setting stays as it is, and later background calls skip the lowering.
            remember_background_rejection(self.plan.model_ref)
        else:
            suppress_reasoning_override(_active_thread_id(), self.plan.model_ref)
        logger.warning(
            "reasoning_fallback: retrying provider default provider=%s model=%s status=%s class=%s scope=%s",
            self.provider_id,
            self.plan.model_ref,
            _status_code(exc),
            type(exc).__name__,
            self.plan.scope or "conversation",
        )

    def _fallback_succeeded(self) -> None:
        if self.plan.is_background:
            return
        thread_id = _active_thread_id()
        selected = self.plan.selection.label
        try:
            if thread_id:
                from row_bot.threads import set_thread_reasoning_selection

                set_thread_reasoning_selection(thread_id, self.plan.model_ref, None)
            forget_reasoning_choice(self.plan.model_ref, self.plan.selection)
        except Exception:
            logger.warning(
                "reasoning_fallback: selection reset failed provider=%s model=%s",
                self.provider_id,
                self.plan.model_ref,
            )
        queue_reasoning_notice(
            thread_id,
            {
                "kind": "reasoning_fallback",
                "model_ref": self.plan.model_ref,
                "selection": selected,
                "message": f"{selected} reasoning wasn't accepted; Provider default was used.",
            },
        )


def should_retry_reasoning_error(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.CancelledError, TimeoutError)):
        return False
    scope = current_cancellation_scope()
    if scope is not None and scope.is_cancelled():
        return False
    status = _status_code(exc)
    if status not in {400, 422}:
        return False
    if is_thinking_binding_error(exc):
        # A replayed thinking block's signature, or the binding controls, were
        # rejected. The direct Anthropic transport already made its one
        # strip-and-retry inside the primary call. Provider default would replay
        # the same history and fail the same way, and it would wrongly reset the
        # user's reasoning choice, so this is never a reasoning fallback.
        return False
    if is_encrypted_reasoning_rejection(exc):
        # Replayed encrypted reasoning was unreadable. The Responses transports
        # already retried once without it; this isn't about the effort chosen.
        return False
    detail = _safe_error_detail(exc).lower()
    return any(term in detail for term in _REASONING_ERROR_TERMS)


def _status_code(exc: BaseException) -> int:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        try:
            status = int(candidate or 0)
        except (TypeError, ValueError):
            status = 0
        if status:
            return status
    text = str(exc).lower()
    for status in (400, 422):
        if f"http {status}" in text or f"status code: {status}" in text or f"status_code={status}" in text:
            return status
    return 0


def _safe_error_detail(exc: BaseException) -> str:
    values = [str(exc)]
    response = getattr(exc, "response", None)
    try:
        payload = response.json() if response is not None else None
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            values.extend(str(error.get(key) or "") for key in ("type", "code", "param", "message"))
        else:
            values.append(str(error or ""))
        values.extend(str(payload.get(key) or "") for key in ("type", "code", "param", "message"))
    return " ".join(values)


def _active_thread_id() -> str:
    try:
        from row_bot.agent import get_current_thread_id

        return get_current_thread_id()
    except Exception:
        return ""


def _bind_tools(model: Any, tools: Sequence[Any], *, tool_choice: Any | None, **kwargs: Any) -> Any:
    bind_tools = getattr(model, "bind_tools", None)
    if callable(bind_tools):
        return bind_tools(tools, tool_choice=tool_choice, **kwargs)
    return model.bind(tools=tools, tool_choice=tool_choice, **kwargs)


def _chat_result(message: Any) -> ChatResult:
    if isinstance(message, AIMessage):
        return ChatResult(generations=[ChatGeneration(message=message)])
    return ChatResult(generations=[ChatGeneration(message=AIMessage(content=str(message or "")))])


def _generation_chunk(message: Any) -> ChatGenerationChunk:
    if isinstance(message, AIMessageChunk):
        return ChatGenerationChunk(message=message)
    if isinstance(message, AIMessage):
        return ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content,
                additional_kwargs=message.additional_kwargs,
                response_metadata=message.response_metadata,
                tool_call_chunks=getattr(message, "tool_call_chunks", []),
                usage_metadata=message.usage_metadata,
            )
        )
    return ChatGenerationChunk(message=AIMessageChunk(content=str(message or "")))
