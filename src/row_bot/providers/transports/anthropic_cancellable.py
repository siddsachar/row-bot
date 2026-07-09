"""Cancellable LangChain Anthropic wrapper.

``langchain-anthropic`` does not expose ``http_client`` as a
``ChatAnthropic`` constructor field in Row-Bot's locked version. Passing that
kwarg is treated as a model request kwarg, not as transport configuration. This
subclass keeps LangChain's Anthropic message/tool handling while constructing
the underlying Anthropic SDK clients with Row-Bot cancellable httpx clients.
"""

from __future__ import annotations

import os
from functools import cached_property
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic

from row_bot.providers.transports.cancellable_http import (
    cancellable_async_http_client,
    cancellable_http_client,
)


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
    """``ChatAnthropic`` with HTTP responses registered for Stop cancellation."""

    @cached_property
    def _client(self) -> anthropic.Client:
        client_params = self._client_params
        return anthropic.Client(
            **client_params,
            http_client=cancellable_http_client(
                **_http_client_kwargs(client_params, self.anthropic_proxy)
            ),
        )

    @cached_property
    def _async_client(self) -> anthropic.AsyncClient:
        client_params = self._client_params
        return anthropic.AsyncClient(
            **client_params,
            http_client=cancellable_async_http_client(
                **_http_client_kwargs(client_params, self.anthropic_proxy)
            ),
        )
