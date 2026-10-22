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

When Ollama can't hand back a tool call, the model is asked once more instead
of the turn ending:

- a slightly malformed call (Qwen3.5's XML tool format) makes Ollama's parser
  answer HTTP 500 ("XML syntax error …") before anything streams. The model is
  asked again with a short note on writing the call;
- a call to a tool that isn't bound in the request (a deferred tool behind
  ``tool_search``/``tool_invoke``, called by its own name) is dropped, and the
  reply comes back empty although tokens were generated. The model is asked
  again with the deferred tools (``deferred_tools.offered()``) bound as bare
  stubs, so Ollama keeps the call, and a call to a stub is handed on as
  ``tool_invoke(name, arguments)``: the bridge checks the arguments against the
  tool's schema and keeps its approvals. Without deferred tools or
  ``tool_invoke``, the model gets a short note instead.

Only those two cases, only while tools are bound, at most once each, and only
before anything of the reply has been passed on. Every attempt's tokens count.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_ollama import ChatOllama
from langchain_ollama.chat_models import _parse_arguments_from_tool_call
from ollama import ResponseError
from pydantic import Field

from row_bot.providers import deferred_tools

logger = logging.getLogger(__name__)

OLLAMA_CHAT_KEEP_ALIVE = "30m"

# What Ollama's tool-call parsers say when the model's own call can't be read.
_TOOL_CALL_PARSE_ERRORS = ("xml syntax error", "error parsing tool call", "failed to parse tool call")
# Measured on a captured request whose malformed call Ollama answers with a 500 (a Qwen3.5-2B fine-tune, Ollama 0.40.7):
# asked again with this note, 35 of 60 calls came back well formed, against 18 of 60 asked again without one.
# A note showing a whole call made the model copy it; one showing only the form did no better than none.
MALFORMED_CALL_NOTE = (
    "(Row-Bot: your last tool call wasn't well formed, so it couldn't be read. Write it again with one "
    "<parameter=…> block for each argument; an object argument is JSON inside its block, for example "
    '<parameter=arguments>\n{"key": "value"}\n</parameter>)'
)
EMPTY_REPLY_NOTE = (
    "(Row-Bot: your last reply came back empty. To use a tool that isn't in your tool list, "
    "call it through tool_invoke with its name and arguments; otherwise answer, or call one of your tools.)"
)
EMPTY_REPLY_NOTE_WITHOUT_BRIDGE = (
    "(Row-Bot: your last reply came back empty. Answer, or call one of the tools in your list.)"
)


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


def _is_tool_call_parse_error(error: BaseException) -> bool:
    """Ollama refused the reply because the model's tool call couldn't be parsed."""
    if not isinstance(error, ResponseError) or getattr(error, "status_code", None) != 500:
        return False
    text = str(getattr(error, "error", "") or error).lower()
    return any(marker in text for marker in _TOOL_CALL_PARSE_ERRORS)


def _shows_something(response: Any) -> bool:
    """The response carries part of the reply: text, reasoning or a tool call."""
    if isinstance(response, str):
        return True
    message = response["message"] if "message" in response else None
    if message is None:
        return False
    return bool(message.get("content") or message.get("thinking") or message.get("tool_calls"))


def _counts(responses: list[Any]) -> tuple[int, int]:
    """The prompt and generated token counts the responses report."""
    prompt = generated = 0
    for response in responses:
        if isinstance(response, str):
            continue
        prompt += int(response.get("prompt_eval_count") or 0)
        generated += int(response.get("eval_count") or 0)
    return prompt, generated


def _with_counts(response: Any, carried: tuple[int, int]) -> Any:
    """``response`` with an earlier, discarded attempt's token counts added to its own."""
    if carried == (0, 0) or isinstance(response, str) or not response.get("done"):
        return response
    response = copy.deepcopy(response)
    response["prompt_eval_count"] = int(response.get("prompt_eval_count") or 0) + carried[0]
    response["eval_count"] = int(response.get("eval_count") or 0) + carried[1]
    return response


def _bound_names(tools: Any) -> set[str]:
    names = set()
    for tool in tools or ():
        function = tool.get("function") if isinstance(tool, Mapping) else None
        if isinstance(function, Mapping):
            names.add(str(function.get("name") or ""))
    return names


def _stub(name: str) -> dict[str, Any]:
    """A bare entry for a deferred tool: enough for Ollama's parser to keep a call to it."""
    return {
        "type": "function",
        "function": {"name": name, "description": "", "parameters": {"type": "object", "properties": {}}},
    }


def _through_bridge(response: Any, stubbed: frozenset[str]) -> Any:
    """``response`` with each call to a stubbed tool made through ``tool_invoke`` instead."""
    if not stubbed or isinstance(response, str) or "message" not in response:
        return response
    calls = response["message"].get("tool_calls") or ()
    if not any(str((call.get("function") or {}).get("name") or "") in stubbed for call in calls):
        return response
    response = copy.deepcopy(response)
    for call in response["message"].get("tool_calls") or ():
        function = call.get("function")
        name = str((function or {}).get("name") or "")
        if name not in stubbed:
            continue
        arguments = function.get("arguments")
        function["name"] = "tool_invoke"
        function["arguments"] = {"name": name, "arguments": dict(arguments) if isinstance(arguments, Mapping) else {}}
    return response


class _Attempt:
    """One request's responses, held back until the reply shows something, so a request asked
    again never repeats what was already passed on."""

    def __init__(self) -> None:
        self.held: list[Any] = []
        self.shown = False

    def take(self, response: Any) -> list[Any]:
        if self.shown:
            return [response]
        if _shows_something(response):
            self.shown = True
            ready, self.held = [*self.held, response], []
            return ready
        self.held.append(response)
        return []

    def came_back_empty(self) -> bool:
        """Tokens were generated but nothing of the reply arrived (a dropped tool call)."""
        return not self.shown and _counts(self.held)[1] > 0


class _Asking:
    """The requests of one model call: as asked, then at most one more for a call Ollama couldn't
    read and at most one more for a reply that came back empty."""

    def __init__(self, messages: list[BaseMessage], kwargs: dict[str, Any]) -> None:
        self.messages = list(messages)
        self.kwargs = kwargs
        self.tools = kwargs.get("tools")
        self.text_parameters = _text_parameters(self.tools)
        self.stubbed: frozenset[str] = frozenset()
        self.carried = (0, 0)
        self.asked_again: set[str] = set()

    def request(self) -> dict[str, Any]:
        if not self.stubbed:
            return self.kwargs
        return {**self.kwargs, "tools": [*self.tools, *(_stub(name) for name in sorted(self.stubbed))]}

    def read(self, response: Any) -> Any:
        return _keep_text_arguments(_through_bridge(response, self.stubbed), self.text_parameters)

    def counted(self, response: Any) -> Any:
        return _with_counts(response, self.carried)

    def again_after_error(self, error: BaseException, attempt: _Attempt) -> bool:
        """Ask once more, with a note on writing the call, when Ollama couldn't read it."""
        if not self.tools or attempt.shown or "malformed" in self.asked_again or not _is_tool_call_parse_error(error):
            return False
        self.asked_again.add("malformed")
        logger.info("Ollama couldn't read the model's tool call; asking it once more")
        self.messages.append(HumanMessage(content=MALFORMED_CALL_NOTE))
        return True

    def again_after_empty(self, attempt: _Attempt) -> bool:
        """Ask once more when the reply came back empty: with the deferred tools bound as stubs, so
        Ollama keeps a call to one (it then goes through ``tool_invoke``), or else with a note."""
        if not self.tools or "empty" in self.asked_again or not attempt.came_back_empty():
            return False
        self.asked_again.add("empty")
        prompt, generated = _counts(attempt.held)
        self.carried = (self.carried[0] + prompt, self.carried[1] + generated)
        bound = _bound_names(self.tools)
        deferred = deferred_tools.offered() - bound if "tool_invoke" in bound else frozenset()
        if deferred:
            logger.info("Ollama returned an empty reply; asking once more with %d deferred tools bound", len(deferred))
            self.stubbed = deferred
        else:
            logger.info("Ollama returned an empty reply with tools bound; asking the model once more")
            note = EMPTY_REPLY_NOTE if "tool_invoke" in bound else EMPTY_REPLY_NOTE_WITHOUT_BRIDGE
            self.messages.append(HumanMessage(content=note))
        return True


class RowBotChatOllama(ChatOllama):
    """``ChatOllama`` that sends its ``num_ctx`` and ``keep_alive`` on every request,
    passes text tool arguments through as text, and asks the model once more when
    Ollama can't hand back its tool call."""

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
        asking = _Asking(messages, kwargs)
        while True:
            attempt = _Attempt()
            try:
                for response in super()._create_chat_stream(asking.messages, stop, **asking.request()):
                    for ready in attempt.take(asking.read(response)):
                        yield asking.counted(ready)
            except ResponseError as error:
                if asking.again_after_error(error, attempt):
                    continue
                raise
            if asking.again_after_empty(attempt):
                continue
            for ready in attempt.held:
                yield asking.counted(ready)
            return

    async def _acreate_chat_stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Mapping[str, Any] | str]:
        asking = _Asking(messages, kwargs)
        while True:
            attempt = _Attempt()
            try:
                async for response in super()._acreate_chat_stream(asking.messages, stop, **asking.request()):
                    for ready in attempt.take(asking.read(response)):
                        yield asking.counted(ready)
            except ResponseError as error:
                if asking.again_after_error(error, attempt):
                    continue
                raise
            if asking.again_after_empty(attempt):
                continue
            for ready in attempt.held:
                yield asking.counted(ready)
            return
