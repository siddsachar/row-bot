"""A local Ollama model's text arguments reach the tool as the text the model wrote.

langchain-ollama parses any string tool argument that is valid JSON (a band-aid for
ollama#6155), so a local model asked to write ``[{"a": 1}]`` to a ``.json`` file used to
hand the file tool a list, fail its validation and write nothing. These tests drive the
local chat model against a fake Ollama on loopback that answers with a tool call.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from row_bot.providers.transports.ollama_local import RowBotChatOllama
from tests.helpers.fake_ollama import fake_streaming_ollama

MODEL = "fake-local:7b"
JSON_TEXT = '[\n  {"name": "Ada", "score": 3},\n  {"name": "Grace", "score": 5}\n]'


class Filters(BaseModel):
    status: str
    labels: list[str] = []


def _write_file(file_path: str, text: str, append: bool = False) -> str:
    return "ok"


def _search_issues(query: str, filters: Filters, note: str | None = None) -> str:
    return "ok"


WRITE_FILE = StructuredTool.from_function(_write_file, name="workspace_write_file", description="Write a file.")
SEARCH_ISSUES = StructuredTool.from_function(_search_issues, name="search_issues", description="Search issues.")


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"function": {"name": name, "arguments": arguments}}


def _ask(tool_calls: list[dict], *, tools: list | None = None, use_async: bool = False) -> list[dict]:
    with fake_streaming_ollama(tokens=[], model=MODEL, tool_calls=tool_calls) as server:
        chat = RowBotChatOllama(model=MODEL, base_url=server.url).bind_tools(tools or [WRITE_FILE, SEARCH_ISSUES])
        prompt = [HumanMessage(content="go")]
        reply = asyncio.run(chat.ainvoke(prompt)) if use_async else chat.invoke(prompt)
    return reply.tool_calls


@pytest.mark.parametrize("use_async", [False, True], ids=["invoke", "ainvoke"])
def test_json_text_for_a_text_parameter_stays_the_models_text(use_async: bool) -> None:
    (call,) = _ask([_call("workspace_write_file", {"file_path": "scores.json", "text": JSON_TEXT})], use_async=use_async)

    assert call["name"] == "workspace_write_file"
    assert call["args"] == {"file_path": "scores.json", "text": JSON_TEXT}
    # The tool accepts it and would write exactly what the model wrote.
    assert WRITE_FILE.invoke(call["args"]) == "ok"


def test_an_optional_text_parameter_keeps_its_text_too() -> None:
    (call,) = _ask([_call("search_issues", {"query": "open", "filters": {"status": "open"}, "note": '{"why": "x"}'})])

    assert call["args"]["note"] == '{"why": "x"}'


def test_an_object_parameter_sent_as_json_text_is_still_parsed() -> None:
    # ollama#6155: some models send an object argument as a JSON string.
    (call,) = _ask([_call("search_issues", {"query": "[bug]", "filters": json.dumps({"status": "open"})})])

    assert call["args"] == {"query": "[bug]", "filters": {"status": "open"}}
    assert SEARCH_ISSUES.invoke(call["args"]) == "ok"


def test_each_call_in_a_reply_is_read_against_its_own_tool() -> None:
    calls = _ask([
        _call("workspace_write_file", {"file_path": "a.json", "text": "{}"}),
        _call("search_issues", {"query": "x", "filters": '{"status": "done", "labels": ["ui"]}'}),
    ])

    assert [call["args"] for call in calls] == [
        {"file_path": "a.json", "text": "{}"},
        {"query": "x", "filters": {"status": "done", "labels": ["ui"]}},
    ]


def test_a_tool_the_model_wasnt_given_is_read_as_before() -> None:
    (call,) = _ask([_call("unknown_tool", {"payload": '{"a": 1}'})])

    assert call["args"] == {"payload": {"a": 1}}
