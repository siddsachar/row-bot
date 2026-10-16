"""``ChatOllama`` for the local Ollama daemon: one runner per model, kept warm.

Ollama reloads a model's runner whenever a request asks for a different
``num_ctx`` than the loaded one (``sched.go`` ``needsReload``), and a request
without ``num_ctx`` asks for the server default. A reload throws away the
prompt cache and re-reads the whole prompt. So every request for a model
carries the ``num_ctx`` it was built with, including calls that pass their own
``options`` (compaction, titles), which would otherwise replace it.

Each request also carries ``keep_alive``: 30 minutes, so a conversation's
pauses don't unload the model and its prompt cache (the server default is
5 minutes). When ``OLLAMA_KEEP_ALIVE`` is set in Row-Bot's environment the
user has chosen a lifetime for the server, so none is sent. Switching the
chat model still unloads the previous one (``models.set_model``).

A tool argument whose schema is text reaches the tool as the text the model
wrote. langchain-ollama parses every string argument that is valid JSON (its
band-aid for ollama#6155, where some models send an object argument as a JSON
string), so a model writing ``[1, 2]`` to a file handed the file tool a list.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_ollama import ChatOllama
from langchain_ollama.chat_models import _parse_arguments_from_tool_call
from pydantic import Field

OLLAMA_CHAT_KEEP_ALIVE = "30m"


def ollama_chat_keep_alive() -> str | None:
    """The ``keep_alive`` Row-Bot sends with chat requests, or ``None`` to defer to the server."""
    if os.environ.get("OLLAMA_KEEP_ALIVE", "").strip():
        return None
    return OLLAMA_CHAT_KEEP_ALIVE


def _schema_types(schema: Any) -> set[str] | None:
    """The JSON types a parameter schema allows, or ``None`` when that isn't plain to see."""
    if not isinstance(schema, Mapping):
        return None
    kinds = schema.get("type")
    types = {kinds} if isinstance(kinds, str) else set(kinds) if isinstance(kinds, list) else set()
    for branch in (*(schema.get("anyOf") or ()), *(schema.get("oneOf") or ())):
        found = _schema_types(branch)
        if found is None:
            return None
        types |= found
    return types or None


def _text_parameters(tools: Any) -> dict[str, frozenset[str]]:
    """Per bound tool, the parameters whose schema allows text and never an object or a list."""
    found: dict[str, frozenset[str]] = {}
    for tool in tools or ():
        function = tool.get("function") if isinstance(tool, Mapping) else None
        parameters = function.get("parameters") if isinstance(function, Mapping) else None
        properties = parameters.get("properties") if isinstance(parameters, Mapping) else None
        if not isinstance(properties, Mapping):
            continue
        names = frozenset(
            str(name)
            for name, schema in properties.items()
            if (types := _schema_types(schema)) and "string" in types and not types & {"object", "array"}
        )
        if names:
            found[str(function.get("name") or "")] = names
    return found


def _text_kept(call: Any, text_parameters: Mapping[str, frozenset[str]]) -> dict[str, str]:
    """The call's string values for its tool's text parameters."""
    function = call.get("function") if call is not None else None
    if function is None:
        return {}
    names = text_parameters.get(str(function.get("name") or ""))
    arguments = function.get("arguments")
    if not names or not isinstance(arguments, Mapping):
        return {}
    return {key: value for key, value in arguments.items() if key in names and isinstance(value, str)}


def _keep_text_arguments(response: Any, text_parameters: Mapping[str, frozenset[str]]) -> Any:
    """``response`` with its tool calls' text arguments kept as the model wrote them.

    The other arguments are read the library's way (string-encoded objects parsed),
    and each call is handed back as one JSON object, which the library loads as is.
    A response with nothing to keep is returned untouched.
    """
    if not text_parameters or isinstance(response, str) or "message" not in response:
        return response
    if not any(_text_kept(call, text_parameters) for call in response["message"].get("tool_calls") or ()):
        return response
    response = copy.deepcopy(response)
    for call in response["message"].get("tool_calls") or ():
        text = _text_kept(call, text_parameters)
        if not text:
            continue
        function = call["function"]
        arguments = function["arguments"]
        others = {key: value for key, value in arguments.items() if key not in text}
        parsed = _parse_arguments_from_tool_call({"function": {"name": function["name"], "arguments": others}}) or {}
        kept = {key: text[key] if key in text else parsed[key] for key in arguments if key in text or key in parsed}
        try:
            function["arguments"] = json.dumps(kept, ensure_ascii=False)
        except (TypeError, ValueError):
            continue  # a value JSON can't carry: leave this call to the library
    return response


class RowBotChatOllama(ChatOllama):
    """``ChatOllama`` that sends its ``num_ctx`` and ``keep_alive`` on every request
    and passes text tool arguments through as text."""

    keep_alive: int | str | None = Field(default_factory=ollama_chat_keep_alive)

    def _chat_params(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        options = kwargs.get("options")
        if isinstance(options, dict) and self.num_ctx and "num_ctx" not in options:
            kwargs["options"] = {**options, "num_ctx": self.num_ctx}
        return super()._chat_params(messages, stop, **kwargs)

    def _create_chat_stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[Mapping[str, Any] | str]:
        text_parameters = _text_parameters(kwargs.get("tools"))
        for response in super()._create_chat_stream(messages, stop, **kwargs):
            yield _keep_text_arguments(response, text_parameters)

    async def _acreate_chat_stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Mapping[str, Any] | str]:
        text_parameters = _text_parameters(kwargs.get("tools"))
        async for response in super()._acreate_chat_stream(messages, stop, **kwargs):
            yield _keep_text_arguments(response, text_parameters)
