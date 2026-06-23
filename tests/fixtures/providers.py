from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from row_bot.providers.models import (
    AuthMethod,
    ModelInfo,
    ModelModality,
    ModelTask,
    ProviderDefinition,
    TransportMode,
)


@dataclass
class FakeAIMessage:
    content: str
    response_metadata: dict[str, Any] = field(default_factory=dict)


class FakeChatModel:
    """Small deterministic chat-model double with LangChain-like methods."""

    def __init__(
        self,
        *,
        model: str = "fake-chat",
        responses: list[str] | None = None,
        stream_chunks: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        self.model = model
        self.kwargs = dict(kwargs)
        self.responses = list(responses or ["fake response"])
        self.stream_chunks = list(stream_chunks or ["fake", " response"])
        self.invocations: list[Any] = []
        self.bound_tools: list[Any] = []

    def invoke(self, messages: Any, **kwargs: Any) -> FakeAIMessage:
        self.invocations.append({"messages": messages, "kwargs": dict(kwargs)})
        content = self.responses.pop(0) if self.responses else "fake response"
        return FakeAIMessage(content, {"model": self.model, "provider": "fake"})

    def stream(self, messages: Any, **kwargs: Any):
        self.invocations.append({"messages": messages, "kwargs": dict(kwargs), "stream": True})
        for chunk in self.stream_chunks:
            yield FakeAIMessage(chunk, {"model": self.model, "provider": "fake"})

    def bind_tools(self, tools: list[Any], **_kwargs: Any) -> "FakeChatModel":
        self.bound_tools = list(tools)
        return self

    def with_structured_output(self, *_args: Any, **_kwargs: Any) -> "FakeChatModel":
        return self


def fake_provider_definition(provider_id: str = "fake") -> ProviderDefinition:
    return ProviderDefinition(
        id=provider_id,
        display_name="Fake Provider",
        auth_methods=(AuthMethod.NONE,),
        default_transport=TransportMode.OPENAI_CHAT,
        base_url="http://127.0.0.1/fake",
        risk_label="local",
        supports_catalog=True,
        icon="science",
    )


def fake_model_info(provider_id: str = "fake", model_id: str = "fake-chat") -> ModelInfo:
    return ModelInfo(
        provider_id=provider_id,
        model_id=model_id,
        display_name="Fake Chat",
        context_window=8192,
        transport=TransportMode.OPENAI_CHAT,
        capabilities=frozenset({"chat", "tools", "streaming", "vision"}),
        input_modalities=frozenset((ModelModality.TEXT.value, ModelModality.IMAGE.value)),
        output_modalities=frozenset((ModelModality.TEXT.value,)),
        tasks=frozenset((ModelTask.CHAT.value,)),
        tool_calling=True,
        streaming=True,
        endpoint_compatibility=frozenset((TransportMode.OPENAI_CHAT,)),
        billing_label="test-only",
        source_confidence="verified",
        risk_label="local",
        source="test-fixture",
    )
