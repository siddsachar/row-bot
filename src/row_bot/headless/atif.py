"""A conversation as an ATIF trajectory: Harbor's Agent Trajectory Interchange Format.

Built from the conversation's own saved messages (its checkpoint), in order:

- the person's message, and the follow-ups Row-Bot sent itself, are ``user`` steps;
- each model reply is an ``agent`` step with its text, reasoning, tool calls and token counts, and the
  results of its tool calls are that step's ``observation`` (each result names the call it answers);
- ``final_metrics`` are the run's totals from the usage ledger: every model call, background ones included.

Field names follow Harbor's published schema (``harbor.models.trajectories``, RFC 0001), version
:data:`SCHEMA_VERSION`, which every Harbor release since then reads.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from row_bot.turn_context import is_turn_context_message

SCHEMA_VERSION = "ATIF-v1.6"
AGENT_NAME = "row-bot"


def trajectory(messages: Iterable[Any], *, session_id: str, agent_version: str, model_name: str,
               prompts: list[str], usage: Mapping[str, Any], extra: Mapping[str, Any] | None = None,
               agent_extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The ATIF document for *messages* (LangChain messages); *prompts* are the messages the run sent, the
    task first, and *usage* the run's ledger totals."""

    steps: list[dict[str, Any]] = []
    by_call: dict[str, dict[str, Any]] = {}
    followups = set(prompts[1:]) - set(prompts[:1])
    for message in messages:
        kind = str(getattr(message, "type", "") or "")
        ui = (getattr(message, "additional_kwargs", None) or {}).get("row_bot_ui") or {}
        if kind == "human":
            text = _text(getattr(message, "content", ""))
            step: dict[str, Any] = {"source": "user", "message": text}
            if is_turn_context_message(message):
                step["extra"] = {"sent_by": "row-bot", "kind": "turn_context"}  # the date, the workspace, the tools
            elif text in followups:
                step["extra"] = {"sent_by": "row-bot"}  # the app's follow-up after a turn, not the task's sender
            steps.append(step)
        elif kind == "ai":
            if isinstance(ui, Mapping) and ui.get("hidden"):
                continue  # Row-Bot's own bookkeeping, never shown or sent as a reply
            steps.append(_agent_step(message, model_name, by_call))
        elif kind == "tool":
            call_id = str(getattr(message, "tool_call_id", "") or "")
            owner = by_call.get(call_id)
            if owner is None:
                continue
            owner.setdefault("observation", {"results": []})["results"].append(
                {"source_call_id": call_id, "content": _text(getattr(message, "content", ""))})
    final = {
        "total_prompt_tokens": int(usage.get("input_tokens") or 0),
        "total_completion_tokens": int(usage.get("output_tokens") or 0),
        "total_cached_tokens": int(usage.get("cache_read_tokens") or 0),
        "total_steps": len(steps),
        "extra": {"model_calls": int(usage.get("calls") or 0)},
    }
    if usage.get("cost_usd") is not None:
        final["total_cost_usd"] = round(float(usage["cost_usd"]), 6)
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "agent": {"name": AGENT_NAME, "version": agent_version, "model_name": model_name,
                  **({"extra": dict(agent_extra)} if agent_extra else {})},
        "steps": [{"step_id": number, **step} for number, step in enumerate(steps, start=1)],
        "final_metrics": final,
    }
    if extra:
        document["extra"] = dict(extra)
    return document


def _agent_step(message: Any, model_name: str, by_call: dict[str, dict[str, Any]]) -> dict[str, Any]:
    metadata = getattr(message, "response_metadata", None) or {}
    step: dict[str, Any] = {
        "source": "agent",
        "model_name": str(metadata.get("model_name") or metadata.get("model") or model_name),
        "message": _text(getattr(message, "content", "")),
    }
    reasoning = _reasoning(message)
    if reasoning:
        step["reasoning_content"] = reasoning
    calls = []
    for call in getattr(message, "tool_calls", None) or []:
        call_id = str(call.get("id") or f"call-{len(by_call) + 1}")
        calls.append({"tool_call_id": call_id, "function_name": str(call.get("name") or ""),
                      "arguments": dict(call.get("args") or {})})
        by_call[call_id] = step
    if calls:
        step["tool_calls"] = calls
    counts = getattr(message, "usage_metadata", None) or {}
    if counts:
        details = counts.get("input_token_details") or {}
        step["metrics"] = {"prompt_tokens": int(counts.get("input_tokens") or 0),
                           "completion_tokens": int(counts.get("output_tokens") or 0),
                           "cached_tokens": int(details.get("cache_read") or 0)}
    return step


def _text(content: Any) -> str:
    """A message's text: a string as it is, or the text parts of a list of content parts."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") if isinstance(part, Mapping) and part.get("type") == "text"
                       else part if isinstance(part, str) else "" for part in content)
    return str(content or "")


def _reasoning(message: Any) -> str:
    kwargs = getattr(message, "additional_kwargs", None) or {}
    text = kwargs.get("reasoning_content")
    if isinstance(text, str) and text.strip():
        return text
    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, Mapping) and part.get("type") in {"reasoning", "thinking"}:
                parts.append(str(part.get("thinking") or part.get("reasoning") or part.get("text") or "")
                             or "".join(str(item.get("text") or "") for item in part.get("summary") or []
                                        if isinstance(item, Mapping)))
        return "".join(parts)
    return ""


__all__ = ["AGENT_NAME", "SCHEMA_VERSION", "trajectory"]
