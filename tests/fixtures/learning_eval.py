"""Fakes for Learning's evaluation tests: a scripted model client, an oracle that plays a
pack task's reference (or decoy) through the harness tools, and captured decisions to replay."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from row_bot.learning.evaluation import ModelReply
from row_bot.learning.packs import PackTask

Respond = Callable[[Sequence[Any], Sequence[Mapping[str, Any]] | None, str | None], AIMessage]


class ScriptedClient:
    """A ``ModelClient`` whose replies come from ``respond(messages, tools, model_ref)``.

    Every call is recorded (``calls``) with what was sent, so tests can check the prefix,
    the tools and the purpose. Each reply reports ``tokens`` (input, output).
    """

    def __init__(self, respond: Respond, *, tokens: tuple[int, int] = (1_000, 100), local: bool = False,
                 model_ref: str = "model:fake:eval") -> None:
        self.respond = respond
        self.tokens = tokens
        self.local = local
        self.model_ref = model_ref
        self.calls: list[dict[str, Any]] = []

    def is_local(self, model_ref: str | None) -> bool:
        return self.local

    def invoke(self, messages: Sequence[Any], *, tools: Sequence[Mapping[str, Any]] | None, model_ref: str | None,
               purpose: str) -> ModelReply:
        self.calls.append({"messages": list(messages), "tools": None if tools is None else list(tools),
                           "model_ref": model_ref, "purpose": purpose})
        message = self.respond(messages, tools, model_ref)
        return ModelReply(message, self.tokens[0], self.tokens[1], self.local, model_ref or self.model_ref)


_ids = itertools.count(1)


def _call(name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": dict(args), "id": f"call_{next(_ids)}", "type": "tool_call"}


def task_of(messages: Sequence[Any], tasks: Sequence[PackTask]) -> PackTask:
    prompt = next(str(m.content) for m in messages if isinstance(m, HumanMessage))
    return next(task for task in tasks if task.prompt == prompt)


def guidance_of(messages: Sequence[Any]) -> str:
    return "\n".join(str(m.content) for m in messages if isinstance(m, SystemMessage))


def oracle(tasks: Sequence[PackTask], *, solution: str = "reference",
           when: Callable[[Sequence[Any], PackTask], str] | None = None) -> Respond:
    """Plays each task's reference (or decoy) through the harness tools: first the files and
    app calls, then the answer. ``when(messages, task)`` may pick the solution per call."""

    def respond(messages: Sequence[Any], _tools: Any, _model: Any) -> AIMessage:
        task = task_of(messages, tasks)
        chosen = when(messages, task) if when is not None else solution
        played = task.reference if chosen == "reference" else task.decoy
        acted = any(isinstance(m, ToolMessage) for m in messages)
        if not acted and (played.files or played.calls):
            calls = [_call("write_file", {"path": path, "content": content}) for path, content in played.files.items()]
            calls += [_call(str(call["tool"]), dict(call.get("args") or {})) for call in played.calls]
            return AIMessage(content="", tool_calls=calls)
        return AIMessage(content=played.answer)

    return respond


def says(text: str) -> Respond:
    return lambda _messages, _tools, _model: AIMessage(content=text)


def calls_tool(name: str, **args: Any) -> Respond:
    return lambda _messages, _tools, _model: AIMessage(content="", tool_calls=[_call(name, args)])


def capture_decision(writer: Any, chats: Any, thread_id: str, *, person: str, answer: AIMessage,
                     earlier: Sequence[Any] = (), tools: Sequence[Mapping[str, Any]] | None = None,
                     model_ref: str = "gpt-test", provider: str = "openai", untrusted_result: str = "") -> str:
    """One real captured model call (through ``Capture``), as the Agent's seam records it.

    Returns its decision id. ``untrusted_result`` adds a tool result stamped untrusted to the
    prefix (web or tool content, E9).
    """

    if thread_id not in chats.threads:
        chats.chat(thread_id)
    turn = writer.begin_turn(thread_id=thread_id, person_message=True)
    messages: list[Any] = [SystemMessage(content="You are Row-Bot."), *earlier]
    if untrusted_result:
        call = _call("read_url", {"url": "https://example.test"})
        messages += [AIMessage(content="", tool_calls=[call]),
                     ToolMessage(content=untrusted_result, tool_call_id=call["id"],
                                 additional_kwargs={"row_bot_untrusted": True})]
    messages.append(HumanMessage(content=person))
    decision = writer.model_call(turn, messages=messages, response=answer, tools=tools, model_ref=model_ref,
                                 provider_id=provider, ledger_call_id="call-1")
    writer.end_turn(turn, "completed")
    writer.drain()
    return str(decision)


def tool_schema(name: str, **properties: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": name.replace("_", " "),
                                             "parameters": {"type": "object",
                                                            "properties": {key: {"type": value} for key, value in
                                                                           properties.items()},
                                                            "required": list(properties)}}}


def all_text(root: Any) -> str:
    """Every byte Learning keeps in learning.db and its blobs, as text (to look for what must not be there)."""

    from tests.fixtures.learning import all_blob_text, all_record_text

    return all_record_text(root) + "\n" + all_blob_text(root)


def results_of(store: Any, run: Any) -> dict[str, Any]:
    data = store.read_blob(run.results_blob or "")
    return json.loads(data.decode("utf-8")) if data else {}


__all__ = [
    "ScriptedClient",
    "all_text",
    "calls_tool",
    "capture_decision",
    "guidance_of",
    "oracle",
    "results_of",
    "says",
    "task_of",
    "tool_schema",
]
