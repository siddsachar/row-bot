from __future__ import annotations

import json
import logging
from contextlib import nullcontext
from typing import Any, Iterator, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from pydantic import Field

logger = logging.getLogger(__name__)


class ChatOpenAICompatible(BaseChatModel):
    """OpenAI-compatible chat transport with endpoint-specific normalization."""

    model_name: str
    api_key: str = "not-needed"
    base_url: str
    endpoint: dict[str, Any] = Field(default_factory=dict)
    timeout: float = 120.0
    http_client: Any | None = None

    @property
    def _llm_type(self) -> str:
        return "openai_compatible_chat"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        return self.bind(tools=[_openai_tool(tool) for tool in tools], tool_choice=tool_choice or "auto", **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        body = self._request_body(messages, stream=False, stop=stop, **kwargs)
        response = self._post(body)
        payload = response.json()
        choice = _first_choice(payload)
        message_payload = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = _choice_content(message_payload, choice)
        tool_calls = _tool_calls_from_openai(message_payload.get("tool_calls") or [])
        metadata = _response_metadata(payload)
        additional_kwargs = {}
        reasoning = _reasoning_content(message_payload, choice)
        if reasoning:
            additional_kwargs["reasoning_content"] = reasoning
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=content, tool_calls=tool_calls, additional_kwargs=additional_kwargs, response_metadata=metadata))],
            llm_output=metadata,
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        if not _endpoint_streaming_supported(self.endpoint):
            yield from self._stream_via_non_stream(messages, stop=stop, **kwargs)
            return
        body = self._request_body(messages, stream=True, stop=stop, **kwargs)
        logger.info(
            "custom_openai_stream: start provider=%s model=%s base_url=%s profile=%s messages=%d roles=%s content_chars=%d tools=%d",
            self.endpoint.get("provider_id") or "custom",
            self.model_name,
            self.base_url,
            self.endpoint.get("profile") or "generic",
            len(body.get("messages") or []),
            ",".join(str(item.get("role") or "?") for item in body.get("messages") or [] if isinstance(item, dict)),
            sum(len(str(item.get("content") or "")) for item in body.get("messages") or [] if isinstance(item, dict)),
            len(body.get("tools") or []),
        )
        content_seen = False
        reasoning_seen = False
        tool_seen = False
        payload_seen = False
        tool_index = 0
        for payload in self._iter_stream_events(body):
            payload_seen = True
            choice = _first_choice(payload)
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            content = str(delta.get("content") or "")
            reasoning = _reasoning_content(delta, choice)
            if content or reasoning:
                content_seen = content_seen or bool(content)
                reasoning_seen = reasoning_seen or bool(reasoning)
                additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}
                chunk = ChatGenerationChunk(message=AIMessageChunk(content=content, additional_kwargs=additional_kwargs))
                if content and run_manager:
                    run_manager.on_llm_new_token(content, chunk=chunk)
                yield chunk
            for call in delta.get("tool_calls") or []:
                chunk_payload = _tool_call_chunk_from_openai(call, tool_index)
                if chunk_payload:
                    tool_seen = True
                    tool_index += 1
                    yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[chunk_payload]))
        if not content_seen and not reasoning_seen and not tool_seen:
            logger.warning(
                "custom_openai_stream: empty stream; retrying non-stream fallback provider=%s model=%s payload_seen=%s",
                self.endpoint.get("provider_id") or "custom",
                self.model_name,
                payload_seen,
            )
            yield from self._stream_via_non_stream(messages, stop=stop, **kwargs)
            return
        if not content_seen:
            logger.warning(
                "custom_openai_stream: completed without content provider=%s model=%s reasoning_seen=%s tool_seen=%s",
                self.endpoint.get("provider_id") or "custom",
                self.model_name,
                reasoning_seen,
                tool_seen,
            )
        else:
            logger.info(
                "custom_openai_stream: complete provider=%s model=%s reasoning_seen=%s tool_seen=%s",
                self.endpoint.get("provider_id") or "custom",
                self.model_name,
                reasoning_seen,
                tool_seen,
            )
        yield ChatGenerationChunk(message=AIMessageChunk(content="", chunk_position="last"))

    def _stream_via_non_stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        logger.info(
            "custom_openai_stream: using non-stream fallback provider=%s model=%s base_url=%s",
            self.endpoint.get("provider_id") or "custom",
            self.model_name,
            self.base_url,
        )
        result = self._generate(messages, stop=stop, **kwargs)
        message = result.generations[0].message if result.generations else AIMessage(content="")
        reasoning = str((getattr(message, "additional_kwargs", None) or {}).get("reasoning_content") or "")
        content = str(getattr(message, "content", "") or "")
        tool_calls = getattr(message, "tool_calls", []) or []
        if reasoning:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", additional_kwargs={"reasoning_content": reasoning}))
        if content or tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content=content, tool_calls=tool_calls))
        if not content and not tool_calls:
            logger.warning(
                "custom_openai_stream: non-stream fallback returned no content provider=%s model=%s reasoning_seen=%s tool_seen=%s",
                self.endpoint.get("provider_id") or "custom",
                self.model_name,
                bool(reasoning),
                bool(tool_calls),
            )
        yield ChatGenerationChunk(message=AIMessageChunk(content="", chunk_position="last"))

    def _request_body(
        self,
        messages: list[BaseMessage],
        *,
        stream: bool,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        accepts_tools = _endpoint_accepts_tools(self.endpoint)
        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": _openai_messages(messages, endpoint=self.endpoint, include_tool_fields=accepts_tools),
            "stream": stream,
        }
        if stop:
            body["stop"] = stop
        extra_body = self.endpoint.get("extra_body")
        if isinstance(extra_body, dict):
            body.update(extra_body)
        self._apply_context_override(body)
        tools = kwargs.get("tools") or []
        if tools and accepts_tools:
            body["tools"] = [_openai_tool(tool) for tool in tools]
            tool_choice = kwargs.get("tool_choice")
            if tool_choice:
                body["tool_choice"] = tool_choice
        for key in ("temperature", "top_p", "max_tokens", "presence_penalty", "frequency_penalty"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        if self.endpoint.get("drop_unsupported_params", True):
            _drop_empty_or_unsupported(body, accepts_tools=accepts_tools)
        return body

    def _post(self, body: dict[str, Any]) -> Any:
        client = self.http_client or _new_http_client(self.timeout)
        owns_client = self.http_client is None
        try:
            response = client.post(
                _chat_url(self.base_url),
                json=body,
                headers=self._headers(),
                timeout=self.timeout,
            )
            _raise_for_status(response, self.endpoint)
            return response
        finally:
            if owns_client:
                client.close()

    def _iter_stream_events(self, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        client = self.http_client or _new_http_client(self.timeout)
        owns_client = self.http_client is None
        raw_lines = 0
        data_lines = 0
        decoded_events = 0
        done_seen = False
        try:
            context = client.stream(
                "POST",
                _chat_url(self.base_url),
                json=body,
                headers=self._headers(),
                timeout=self.timeout,
            ) if hasattr(client, "stream") else nullcontext(client.post(
                _chat_url(self.base_url),
                json=body,
                headers=self._headers(),
                timeout=self.timeout,
            ))
            with context as response:
                _raise_for_status(response, self.endpoint)
                for line in response.iter_lines():
                    if line:
                        raw_lines += 1
                        text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
                        stripped = text.strip()
                        if stripped.startswith("data:"):
                            data_lines += 1
                            if stripped[5:].strip() == "[DONE]":
                                done_seen = True
                    payload = _decode_sse_line(line)
                    if payload:
                        decoded_events += 1
                        yield payload
        finally:
            if decoded_events == 0:
                logger.warning(
                    "custom_openai_stream: no decoded SSE events provider=%s model=%s raw_lines=%d data_lines=%d done_seen=%s",
                    self.endpoint.get("provider_id") or "custom",
                    self.model_name,
                    raw_lines,
                    data_lines,
                    done_seen,
                )
            else:
                logger.debug(
                    "custom_openai_stream: decoded SSE events provider=%s model=%s raw_lines=%d data_lines=%d decoded=%d done_seen=%s",
                    self.endpoint.get("provider_id") or "custom",
                    self.model_name,
                    raw_lines,
                    data_lines,
                    decoded_events,
                    done_seen,
                )
            if owns_client:
                client.close()

    def _headers(self) -> dict[str, str]:
        headers = dict(self.endpoint.get("headers") or {})
        if self.api_key:
            header_name = str(self.endpoint.get("api_key_header") or "Authorization")
            headers[header_name] = f"Bearer {self.api_key}" if header_name.lower() == "authorization" else self.api_key
        headers.setdefault("Accept", "application/json")
        headers.setdefault("Content-Type", "application/json")
        return headers

    def _apply_context_override(self, body: dict[str, Any]) -> None:
        if not self.endpoint.get("supports_runtime_context_override"):
            return
        param_name = str(self.endpoint.get("context_param_name") or "").strip()
        if not param_name:
            return
        try:
            from models import get_context_size
            from providers.selection import model_ref

            provider_id = str(self.endpoint.get("provider_id") or "").strip()
            model_name = model_ref(provider_id, self.model_name) if provider_id else self.model_name
            context_size = get_context_size(model_name)
        except Exception:
            return
        if context_size and context_size > 0:
            body[param_name] = int(context_size)


def _openai_messages(messages: list[BaseMessage], *, endpoint: dict[str, Any], include_tool_fields: bool) -> list[dict[str, Any]]:
    ordered = _system_first(messages) if endpoint.get("system_message_mode") == "system_first" else list(messages)
    return [_openai_message(message, endpoint=endpoint, include_tool_fields=include_tool_fields) for message in ordered]


def _system_first(messages: list[BaseMessage]) -> list[BaseMessage]:
    system = [message for message in messages if isinstance(message, SystemMessage)]
    rest = [message for message in messages if not isinstance(message, SystemMessage)]
    return [*system, *rest]


def _openai_message(message: BaseMessage, *, endpoint: dict[str, Any], include_tool_fields: bool) -> dict[str, Any]:
    role = "user"
    if isinstance(message, SystemMessage):
        role = "system"
    elif isinstance(message, AIMessage):
        role = "assistant"
    elif isinstance(message, ToolMessage):
        role = "tool"
    elif isinstance(message, HumanMessage):
        role = "user"
    content = _message_content(message, endpoint)
    if not include_tool_fields and isinstance(message, ToolMessage):
        name = str(getattr(message, "name", "") or getattr(message, "tool_call_id", "") or "tool")
        return {"role": "user", "content": f"[Tool result from {name}]: {_message_text(message)}"}
    payload: dict[str, Any] = {"role": role, "content": content}
    if include_tool_fields and isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
        payload["tool_calls"] = [_tool_call_to_openai(call, index) for index, call in enumerate(message.tool_calls) if isinstance(call, dict)]
    if include_tool_fields and isinstance(message, ToolMessage):
        payload["tool_call_id"] = str(getattr(message, "tool_call_id", "") or getattr(message, "name", "") or "tool")
    return payload


def _message_content(message: BaseMessage, endpoint: dict[str, Any]) -> Any:
    if endpoint.get("message_content_mode") == "string_text":
        return _message_text(message)
    return message.content


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("input") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content or "")


def _endpoint_accepts_tools(endpoint: dict[str, Any]) -> bool:
    return True


def _endpoint_streaming_supported(endpoint: dict[str, Any]) -> bool:
    probe = endpoint.get("last_probe") if isinstance(endpoint.get("last_probe"), dict) else {}
    if probe.get("streaming_ok") is False:
        return False
    return True


def _drop_empty_or_unsupported(body: dict[str, Any], *, accepts_tools: bool) -> None:
    if not accepts_tools:
        body.pop("tools", None)
        body.pop("tool_choice", None)
    for key in ("parallel_tool_calls", "reasoning", "reasoning_effort", "response_format"):
        if key in body and body[key] in (None, "", {}, []):
            body.pop(key, None)


def _openai_tool(tool: dict[str, Any] | type | Any) -> dict[str, Any]:
    if isinstance(tool, dict) and tool.get("type") == "function" and isinstance(tool.get("function"), dict):
        return tool
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool

        converted = convert_to_openai_tool(tool)
        if isinstance(converted, dict):
            return converted
    except Exception:
        pass
    if isinstance(tool, dict):
        return tool
    name = getattr(tool, "name", tool.__class__.__name__)
    description = getattr(tool, "description", "")
    schema = getattr(tool, "args_schema", None)
    parameters = schema.model_json_schema() if hasattr(schema, "model_json_schema") else {"type": "object", "properties": {}}
    return {"type": "function", "function": {"name": str(name), "description": str(description or ""), "parameters": parameters}}


def _tool_call_to_openai(call: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "id": str(call.get("id") or f"call_{index}"),
        "type": "function",
        "function": {
            "name": str(call.get("name") or ""),
            "arguments": json.dumps(call.get("args") or {}),
        },
    }


def _tool_calls_from_openai(calls: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        args = function.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        result.append({
            "name": str(function.get("name") or ""),
            "args": args if isinstance(args, dict) else {},
            "id": str(call.get("id") or f"call_{index}"),
            "type": "tool_call",
        })
    return result


def _tool_call_chunk_from_openai(call: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(call, dict):
        return None
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(function.get("name") or "").strip()
    arguments = function.get("arguments") or ""
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments)
    call_index = call.get("index")
    if call_index is None:
        call_index = index
    call_id = str(call.get("id") or f"openai_call_{call_index}")
    if not name and not arguments:
        return None
    return {
        "name": name,
        "args": arguments,
        "id": call_id,
        "index": int(call_index),
    }


def _new_http_client(timeout: float) -> Any:
    import httpx

    return httpx.Client(timeout=timeout)


def _chat_url(base_url: str) -> str:
    return f"{str(base_url or '').rstrip('/')}/chat/completions"


def _raise_for_status(response: Any, endpoint: dict[str, Any]) -> None:
    status_code = int(getattr(response, "status_code", 0) or 0)
    if 200 <= status_code < 300 or status_code == 0:
        return
    name = str(endpoint.get("display_name") or endpoint.get("name") or "custom endpoint")
    detail = _safe_response_text(response)
    suffix = f" Details: {detail}" if detail else ""
    if status_code == 400:
        raise RuntimeError(
            f"{name} rejected the chat request (HTTP 400). The endpoint profile or selected model "
            f"may not accept this message/tool payload.{suffix}"
        )
    raise RuntimeError(f"{name} request failed with HTTP {status_code}.{suffix}")


def _safe_response_text(response: Any) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error.get("message"))[:300]
            for key in ("message", "detail"):
                if payload.get(key):
                    return str(payload.get(key))[:300]
            return str(payload)[:300]
    except Exception:
        pass
    return str(getattr(response, "text", "") or "")[:300]


def _first_choice(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices") if isinstance(payload, dict) else []
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0]
    return {}


def _choice_content(message_payload: dict[str, Any], choice: dict[str, Any]) -> str:
    content = message_payload.get("content")
    if isinstance(content, str):
        return content
    text = choice.get("text")
    return str(text or "")


def _reasoning_content(message_payload: dict[str, Any], choice: dict[str, Any]) -> str:
    reasoning = message_payload.get("reasoning_content")
    if isinstance(reasoning, str):
        return reasoning
    reasoning = message_payload.get("reasoning")
    if isinstance(reasoning, str):
        return reasoning
    reasoning = choice.get("reasoning_content")
    return reasoning if isinstance(reasoning, str) else ""


def _response_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if "model" in payload:
        metadata["model"] = payload["model"]
    usage = payload.get("usage")
    if isinstance(usage, dict):
        metadata["token_usage"] = usage
    return metadata


def _decode_sse_line(line: Any) -> dict[str, Any] | None:
    if not line:
        return None
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    text = str(line).strip()
    if text.startswith("data:"):
        text = text[5:].strip()
    if text == "[DONE]":
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
