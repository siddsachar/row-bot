"""Cancellable LangChain OpenRouter wrapper."""

from __future__ import annotations

from typing import Any

from langchain_openrouter import ChatOpenRouter

from row_bot.brand import APP_DISPLAY_NAME, APP_WEBSITE_URL
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
    """``ChatOpenRouter`` with caller-owned cancellable OpenRouter SDK clients."""

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
