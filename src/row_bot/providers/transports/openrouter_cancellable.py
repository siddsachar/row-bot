"""Cancellable LangChain OpenRouter wrapper."""

from __future__ import annotations

from typing import Any

from langchain_openrouter import ChatOpenRouter

from row_bot.providers.transports.cancellable_http import (
    cancellable_async_http_client,
    cancellable_http_client,
)


class CancellableChatOpenRouter(ChatOpenRouter):
    """``ChatOpenRouter`` with caller-owned cancellable OpenRouter SDK clients."""

    def _build_client(self) -> Any:
        import openrouter
        from openrouter.utils import BackoffStrategy, RetryConfig

        client_kwargs: dict[str, Any] = {
            "api_key": self.openrouter_api_key.get_secret_value(),  # type: ignore[union-attr]
        }
        if self.openrouter_api_base:
            client_kwargs["server_url"] = self.openrouter_api_base

        extra_headers: dict[str, str] = {}
        if self.app_url:
            extra_headers["HTTP-Referer"] = self.app_url
        if self.app_title:
            extra_headers["X-Title"] = self.app_title
        if self.app_categories:
            extra_headers["X-OpenRouter-Categories"] = ",".join(self.app_categories)

        http_kwargs: dict[str, Any] = {"follow_redirects": True}
        if extra_headers:
            http_kwargs["headers"] = extra_headers
        client_kwargs["client"] = cancellable_http_client(**http_kwargs)
        client_kwargs["async_client"] = cancellable_async_http_client(**http_kwargs)

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
