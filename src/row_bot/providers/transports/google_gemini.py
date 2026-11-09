"""Google's Gemini chat model with tool schemas Gemini accepts."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Sequence

from row_bot.providers.tool_schema import gemini_tools


@lru_cache(maxsize=4)
def _gemini_class(base: type) -> type:
    class ChatGemini(base):  # type: ignore[misc, valid-type]
        """``ChatGoogleGenerativeAI`` that binds tools without empty choices (Gemini refuses them)."""

        def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
            return super().bind_tools(gemini_tools(tools), **kwargs)

    ChatGemini.__name__ = ChatGemini.__qualname__ = base.__name__
    return ChatGemini


def chat_gemini() -> type:
    """The Gemini chat class, built on the ``langchain_google_genai`` loaded now."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    return _gemini_class(ChatGoogleGenerativeAI)
