"""Harbor's ATIF rules (``harbor.models.trajectories``, RFC 0001), checked without Harbor installed.

``problems(document)`` lists what Harbor's validator would refuse: unknown fields (its models forbid
extras), missing required ones, step ids that don't count up from 1, an unknown source, agent-only fields on
another step, and an observation result that names a tool call its step didn't make.
"""

from __future__ import annotations

from typing import Any

VERSIONS = {f"ATIF-v1.{minor}" for minor in range(9)}
ROOT = {"schema_version", "session_id", "trajectory_id", "agent", "steps", "notes", "final_metrics",
        "continued_trajectory_ref", "extra", "subagent_trajectories"}
AGENT = {"name", "version", "model_name", "tool_definitions", "extra"}
STEP = {"step_id", "timestamp", "source", "message", "model_name", "reasoning_effort", "reasoning_content",
        "tool_calls", "observation", "metrics", "llm_call_count", "is_copied_context", "extra"}
AGENT_ONLY = {"model_name", "reasoning_effort", "reasoning_content", "tool_calls", "metrics"}
TOOL_CALL = {"tool_call_id", "function_name", "arguments", "extra"}
RESULT = {"source_call_id", "content", "subagent_trajectory_ref", "extra"}
METRICS = {"prompt_tokens", "completion_tokens", "cached_tokens", "cost_usd", "prompt_token_ids",
           "completion_token_ids", "logprobs", "extra"}
FINAL = {"total_prompt_tokens", "total_completion_tokens", "total_cached_tokens", "total_cost_usd", "total_steps",
         "extra"}


def _extra_keys(where: str, value: dict[str, Any], allowed: set[str]) -> list[str]:
    return [f"{where}: unknown field {key}" for key in sorted(set(value) - allowed)]


def problems(document: dict[str, Any]) -> list[str]:
    found = _extra_keys("trajectory", document, ROOT)
    if document.get("schema_version") not in VERSIONS:
        found.append(f"schema_version {document.get('schema_version')!r}")
    agent = document.get("agent")
    if not isinstance(agent, dict) or not isinstance(agent.get("name"), str) or not isinstance(agent.get("version"), str):
        found.append("agent needs a name and a version")
    else:
        found += _extra_keys("agent", agent, AGENT)
    steps = document.get("steps")
    if not isinstance(steps, list):
        return [*found, "steps is not a list"]
    for index, step in enumerate(steps, start=1):
        where = f"step {index}"
        found += _extra_keys(where, step, STEP)
        if step.get("step_id") != index:
            found.append(f"{where}: step_id {step.get('step_id')!r}")
        if step.get("source") not in {"system", "user", "agent"}:
            found.append(f"{where}: source {step.get('source')!r}")
        if not isinstance(step.get("message"), (str, list)):
            found.append(f"{where}: message missing")
        if step.get("source") != "agent":
            found += [f"{where}: {key} on a {step.get('source')} step" for key in sorted(AGENT_ONLY & set(step))]
        call_ids = set()
        for call in step.get("tool_calls") or []:
            found += _extra_keys(f"{where} tool call", call, TOOL_CALL)
            if not (isinstance(call.get("tool_call_id"), str) and isinstance(call.get("function_name"), str)
                    and isinstance(call.get("arguments"), dict)):
                found.append(f"{where}: a tool call needs tool_call_id, function_name and arguments")
            call_ids.add(call.get("tool_call_id"))
        observation = step.get("observation")
        if observation is not None:
            if set(observation) - {"results"} or not isinstance(observation.get("results"), list):
                found.append(f"{where}: observation is {{results: [...]}}")
            for result in observation.get("results") or []:
                found += _extra_keys(f"{where} result", result, RESULT)
                if result.get("source_call_id") is not None and result["source_call_id"] not in call_ids:
                    found.append(f"{where}: a result answers {result['source_call_id']!r}, a call it didn't make")
        if isinstance(step.get("metrics"), dict):
            found += _extra_keys(f"{where} metrics", step["metrics"], METRICS)
    final = document.get("final_metrics")
    if final is not None:
        found += _extra_keys("final_metrics", final, FINAL)
    return found


__all__ = ["problems"]
