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

Stop ends a request at once, even one Ollama hasn't answered yet: while it
parses a tool call Ollama sends nothing, not even headers, so a model that
opens a call and never closes it generates for as long as nobody hangs up
(one ran 30 minutes). Each request registers with the run's cancellation scope
when it starts, and Stop shuts its connection down, which ends the generation;
a call that Stop ended passes nothing more on and isn't asked again. Two
backstops for runs nobody is watching:

- a reply is never longer than the context window (``num_predict`` is the
  request's ``num_ctx`` unless the caller sets one): a reply that long no
  longer fits beside the model's own prompt, nor in the next step's request;
- a request that receives nothing for ``LOCAL_CHAT_IDLE_SECONDS`` ends with
  ``OllamaSilentError``, which says so in plain words, and so does a reply
  that reached its length limit with nothing of it shown (it isn't asked
  again: a model stuck in a tool call fills the window again).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import socket
import threading
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from typing import Any

import httpx
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_ollama import ChatOllama
from langchain_ollama.chat_models import _parse_arguments_from_tool_call
from ollama import ResponseError
from pydantic import Field, model_validator

from row_bot.cancellation import CancellationScope, current_cancellation_scope
from row_bot.providers import deferred_tools

logger = logging.getLogger(__name__)

OLLAMA_CHAT_KEEP_ALIVE = "30m"
# The longest a local chat request may receive nothing. Ollama is silent while it loads the model (it gives up on a
# load that stalls for 5 minutes), reads the prompt and writes a tool call, which it holds back until the call is
# complete. 10 minutes leaves room for all three on slow hardware, while a model stuck in a tool call it never
# closes no longer holds the GPU for half an hour.
LOCAL_CHAT_IDLE_SECONDS = 600.0
# One connection per request, so Stop can find the request's own socket (connections are local and cheap).
_ONE_REQUEST_PER_CONNECTION = httpx.Limits(max_keepalive_connections=0)
_REQUEST_ENDED = frozenset({
    "http11.response_closed.complete", "http11.response_closed.failed", "connection.connect_tcp.failed",
})

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


class OllamaSilentError(RuntimeError):
    """The local model's reply never came: Ollama sent nothing for longer than the request's idle limit, or the
    model wrote until the reply's length limit without any of it showing (a tool call it never closed)."""

    @classmethod
    def idle(cls, seconds: float | None) -> OllamaSilentError:
        span = "too long" if not seconds else f"{seconds / 60:g} minutes" if seconds >= 60 else f"{seconds:g} seconds"
        return cls(f"The local model sent nothing for {span}, so Row-Bot stopped waiting for it. "
                   "Try again, or try another model.")

    @classmethod
    def filled_window(cls) -> OllamaSilentError:
        return cls("The local model filled its whole context window without sending a reply, so Row-Bot stopped it. "
                   "Try again, or try another model.")


def ollama_chat_keep_alive() -> str | None:
    """The ``keep_alive`` Row-Bot sends with chat requests, or ``None`` to defer to the server."""
    if os.environ.get("OLLAMA_KEEP_ALIVE", "").strip():
        return None
    return OLLAMA_CHAT_KEEP_ALIVE


def _shut(sock: Any) -> None:
    # A shutdown, not a close: only a shutdown wakes a read blocked in another thread on Linux, and it tells
    # Ollama the client has gone.
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except (AttributeError, OSError):
        pass  # never connected, or already closed


class _LocalRequest:
    """One request to Ollama, which Stop ends by shutting its connection down, connected yet or not."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._socket: Any = None
        self._stopped = False
        self.unregister: Callable[[], None] = lambda: None

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            sock = self._socket
        _shut(sock)

    def trace(self, event: str, info: Mapping[str, Any]) -> None:
        """httpcore's ``trace`` extension: learns the request's socket, and when the request is over."""
        if event == "connection.connect_tcp.complete":
            sock = info["return_value"].get_extra_info("socket")
            with self._lock:
                self._socket, stopped = sock, self._stopped
            if stopped:
                _shut(sock)
        elif event in _REQUEST_ENDED:
            self.unregister()

    async def atrace(self, event: str, info: Mapping[str, Any]) -> None:
        self.trace(event, info)


def _stoppable(request: httpx.Request, *, asynchronous: bool) -> None:
    scope = current_cancellation_scope()
    if scope is None:
        return
    local = _LocalRequest()
    request.extensions["trace"] = local.atrace if asynchronous else local.trace
    local.unregister = scope.register(local.stop, "ollama_local_request.stop")


def _request_started(request: httpx.Request) -> None:
    _stoppable(request, asynchronous=False)


async def _arequest_started(request: httpx.Request) -> None:
    _stoppable(request, asynchronous=True)


def _raise_if_stopped(scope: CancellationScope | None, cause: BaseException | None = None) -> None:
    """End the call once Stop was pressed: nothing more of the reply is passed on, and it isn't asked again."""
    if scope is not None and scope.is_cancelled():
        raise InterruptedError("execution_cancelled") from cause


def _silent_for(error: httpx.ReadTimeout) -> float | None:
    try:
        return error.request.extensions["timeout"]["read"]
    except (KeyError, RuntimeError, TypeError):
        return None


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

    def ran_out(self) -> bool:
        """The reply reached its length limit with nothing of it shown: asked again, it would only do so again."""
        return not self.shown and any(
            not isinstance(response, str) and response.get("done_reason") == "length" for response in self.held)


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
    passes text tool arguments through as text, asks the model once more when
    Ollama can't hand back its tool call, and can be stopped mid-request."""

    keep_alive: int | str | None = Field(default_factory=ollama_chat_keep_alive)

    @model_validator(mode="before")
    @classmethod
    def _stoppable_requests(cls, data: Any) -> Any:
        """Each client registers its requests with Stop, opens one connection per request and, unless the
        caller set a timeout, gives up on a request that receives nothing for ``LOCAL_CHAT_IDLE_SECONDS``."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        shared = data.get("client_kwargs") or {}
        for key, hook in (("sync_client_kwargs", _request_started), ("async_client_kwargs", _arequest_started)):
            own = dict(data.get(key) or {})
            hooks = dict(own.get("event_hooks") or shared.get("event_hooks") or {})
            hooks["request"] = [*(known for known in hooks.get("request", ()) if known is not hook), hook]
            own.update(event_hooks=hooks, limits=_ONE_REQUEST_PER_CONNECTION)
            if "timeout" not in own and "timeout" not in shared:
                own["timeout"] = LOCAL_CHAT_IDLE_SECONDS
            data[key] = own
        return data

    def _chat_params(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        options = kwargs.get("options")
        if isinstance(options, dict) and self.num_ctx and "num_ctx" not in options:
            kwargs["options"] = {**options, "num_ctx": self.num_ctx}
        params = super()._chat_params(messages, stop, **kwargs)
        options = params.get("options")
        if isinstance(options, dict) and options.get("num_ctx") and "num_predict" not in options:
            params["options"] = {**options, "num_predict": options["num_ctx"]}
        return params

    def _create_chat_stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[Mapping[str, Any] | str]:
        scope = current_cancellation_scope()
        asking = _Asking(messages, kwargs)
        while True:
            _raise_if_stopped(scope)
            attempt = _Attempt()
            try:
                for response in super()._create_chat_stream(asking.messages, stop, **asking.request()):
                    for ready in attempt.take(asking.read(response)):
                        yield asking.counted(ready)
            except Exception as error:
                _raise_if_stopped(scope, error)
                if isinstance(error, httpx.ReadTimeout):
                    raise OllamaSilentError.idle(_silent_for(error)) from error
                if asking.again_after_error(error, attempt):
                    continue
                raise
            _raise_if_stopped(scope)
            if attempt.ran_out():
                raise OllamaSilentError.filled_window()
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
        scope = current_cancellation_scope()
        asking = _Asking(messages, kwargs)
        while True:
            _raise_if_stopped(scope)
            attempt = _Attempt()
            try:
                async for response in super()._acreate_chat_stream(asking.messages, stop, **asking.request()):
                    for ready in attempt.take(asking.read(response)):
                        yield asking.counted(ready)
            except Exception as error:
                _raise_if_stopped(scope, error)
                if isinstance(error, httpx.ReadTimeout):
                    raise OllamaSilentError.idle(_silent_for(error)) from error
                if asking.again_after_error(error, attempt):
                    continue
                raise
            _raise_if_stopped(scope)
            if attempt.ran_out():
                raise OllamaSilentError.filled_window()
            if asking.again_after_empty(attempt):
                continue
            for ready in attempt.held:
                yield asking.counted(ready)
            return
