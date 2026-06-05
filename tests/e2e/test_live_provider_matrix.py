from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


pytestmark = pytest.mark.live_provider


ACCEPTABLE_ERROR_RE = re.compile(
    r"credit|quota|rate.?limit|insufficient|balance|billing|payment|capacity|"
    r"not configured|credentials|not_running|connection refused|unable to connect|"
    r"connection error|timeout|timed out|model unavailable|not found|404",
    re.IGNORECASE,
)


def _enabled() -> bool:
    return os.environ.get("ROW_BOT_LIVE_PROVIDER_E2E", "").strip().lower() in {"1", "true", "yes"}


def _report_path() -> Path:
    return Path(os.environ.get("ROW_BOT_LIVE_PROVIDER_REPORT", "test-results/live_provider_matrix.json"))


def _event_summary(events: list[tuple[str, Any]]) -> dict[str, Any]:
    answer = ""
    errors: list[str] = []
    tool_calls = 0
    tool_results = 0
    thinking_chars = 0
    for event_type, payload in events:
        if event_type == "done":
            answer = str(payload or "")
        elif event_type == "error":
            errors.append(str(payload or ""))
        elif event_type == "tool_call":
            tool_calls += 1
        elif event_type == "tool_done":
            tool_results += 1
        elif event_type == "thinking_token":
            thinking_chars += len(str(payload or ""))
    status = "error" if errors else "done" if any(event[0] == "done" for event in events) else "unknown"
    return {
        "status": status,
        "answer_chars": len(answer),
        "errors": errors,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "thinking_chars": thinking_chars,
        "event_types": [event[0] for event in events],
    }


def _run_agent_case(agent_module, *, model_ref: str, provider_id: str, prompt: str, tools: list[str], label: str) -> dict[str, Any]:
    thread_id = f"live_{provider_id}_{label}_{int(time.time() * 1000)}"
    config = {"configurable": {"thread_id": thread_id, "model_override": model_ref, "runtime_mode": "agent"}}
    started = time.time()
    try:
        events = list(agent_module.stream_agent(prompt, tools, config))
        result = _event_summary(events)
        result.update({
            "label": label,
            "thread_id": thread_id,
            "duration_s": round(time.time() - started, 3),
        })
        return result
    except Exception as exc:  # readiness/transport setup can fail before events
        return {
            "label": label,
            "thread_id": thread_id,
            "duration_s": round(time.time() - started, 3),
            "status": "exception",
            "answer_chars": 0,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "tool_calls": 0,
            "tool_results": 0,
            "thinking_chars": 0,
            "event_types": [],
        }


def _poison_thread(thread_id: str) -> None:
    from row_bot.threads import append_checkpoint_messages

    append_checkpoint_messages(
        thread_id,
        [
            HumanMessage(content="Hi. what tools do you have? use the row bot status tool to check"),
            AIMessage(
                content="",
                tool_calls=[{"name": "row_bot_status", "args": {}, "id": "call_1", "type": "tool_call"}],
                invalid_tool_calls=[{"name": "", "args": '"category":"tools"}', "id": "openai_call_0", "error": None}],
                additional_kwargs={"reasoning_content": "I should use the tool."},
            ),
            ToolMessage(content="repair", name="row_bot_status", tool_call_id="call_1"),
            AIMessage(
                content="",
                tool_calls=[{"name": "row_bot_status", "args": {"category": "overview"}, "id": "text_call_0", "type": "tool_call"}],
                additional_kwargs={
                    "reasoning_content": (
                        "<tool_call><function=row_bot_status><parameter=category>"
                        "overview</parameter></function></tool_call>"
                    )
                },
            ),
            ToolMessage(content="overview", name="row_bot_status", tool_call_id="text_call_0"),
            AIMessage(
                content="",
                tool_calls=[{"name": "row_bot_status", "args": {"category": "tools"}, "id": "text_call_0", "type": "tool_call"}],
                additional_kwargs={
                    "reasoning_content": (
                        "<tool_call><function=row_bot_status><parameter=category>"
                        "tools</parameter></function></tool_call>"
                    )
                },
            ),
            ToolMessage(content="tools", name="row_bot_status", tool_call_id="text_call_0"),
            AIMessage(content="I found the tools.", additional_kwargs={"reasoning_content": "Now answer."}),
        ],
    )


def _run_poisoned_case(agent_module, *, model_ref: str, provider_id: str) -> dict[str, Any]:
    thread_id = f"live_{provider_id}_poison_{int(time.time() * 1000)}"
    _poison_thread(thread_id)
    config = {"configurable": {"thread_id": thread_id, "model_override": model_ref, "runtime_mode": "agent"}}
    started = time.time()
    try:
        events = list(agent_module.stream_agent("Reply with exactly: replay ok", [], config))
        result = _event_summary(events)
    except Exception as exc:
        result = {
            "status": "exception",
            "answer_chars": 0,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "tool_calls": 0,
            "tool_results": 0,
            "thinking_chars": 0,
            "event_types": [],
        }
    diagnostics_before = agent_module._provider_transcript_diagnostics(__import__("threads").get_latest_checkpoint_messages(thread_id))
    diagnostics_after = agent_module._provider_transcript_diagnostics(
        agent_module._normalize_provider_facing_messages(
            __import__("threads").get_latest_checkpoint_messages(thread_id),
            provider_id=provider_id,
        )
    )
    result.update({
        "label": "poisoned_replay",
        "thread_id": thread_id,
        "duration_s": round(time.time() - started, 3),
        "transcript_before": diagnostics_before,
        "transcript_after": diagnostics_after,
    })
    return result


def _discover_live_candidates() -> list[dict[str, str]]:
    from row_bot.providers.runtime import list_configured_provider_ids, provider_status
    from row_bot.providers.selection import list_quick_choices, model_ref
    from row_bot.providers.custom import list_custom_endpoints

    configured = set(list_configured_provider_ids())
    candidates: dict[str, dict[str, str]] = {}
    for choice in list_quick_choices("chat", include_inactive=True):
        if choice.get("kind") != "model":
            continue
        provider_id = str(choice.get("provider_id") or "")
        model_id = str(choice.get("model_id") or "")
        if not provider_id or not model_id or provider_id not in configured:
            continue
        status = provider_status(provider_id)
        if not status.get("configured"):
            continue
        candidates.setdefault(provider_id, {
            "provider_id": provider_id,
            "model_ref": str(choice.get("id") or model_ref(provider_id, model_id)),
            "model_id": model_id,
            "display_name": str(choice.get("display_name") or model_id),
        })

    for endpoint in list_custom_endpoints():
        provider_id = str(endpoint.get("provider_id") or "")
        if not provider_id or provider_id in candidates:
            continue
        models = endpoint.get("models") if isinstance(endpoint.get("models"), list) else []
        model_id = ""
        for item in models:
            if isinstance(item, dict):
                model_id = str(item.get("id") or item.get("model_id") or "")
                if model_id:
                    break
        if model_id:
            candidates[provider_id] = {
                "provider_id": provider_id,
                "model_ref": model_ref(provider_id, model_id),
                "model_id": model_id,
                "display_name": str(endpoint.get("display_name") or endpoint.get("name") or model_id),
            }

    return sorted(candidates.values(), key=lambda item: item["provider_id"])


def _classify_result(case: dict[str, Any]) -> str:
    if case.get("status") == "done" and case.get("answer_chars", 0) > 0:
        return "pass"
    text = " ".join(str(error) for error in case.get("errors") or [])
    if ACCEPTABLE_ERROR_RE.search(text):
        return "acceptable_error"
    return "unexpected_error"


@pytest.mark.skipif(not _enabled(), reason="set ROW_BOT_LIVE_PROVIDER_E2E=1 to run real provider calls")
def test_live_configured_provider_matrix():
    import row_bot.agent as agent

    candidates = _discover_live_candidates()
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "candidates": candidates,
        "providers": [],
    }
    unexpected: list[str] = []

    for candidate in candidates:
        provider_id = candidate["provider_id"]
        model_ref_value = candidate["model_ref"]
        provider_report = dict(candidate)
        provider_report["cases"] = []
        for case in [
            _run_agent_case(
                agent,
                model_ref=model_ref_value,
                provider_id=provider_id,
                prompt="Reply with exactly: provider smoke ok",
                tools=[],
                label="simple_reply",
            ),
            _run_agent_case(
                agent,
                model_ref=model_ref_value,
                provider_id=provider_id,
                prompt="Use row_bot_status with category tools, then answer with only the enabled and disabled tool counts.",
                tools=["row_bot_status"],
                label="tool_call",
            ),
            _run_poisoned_case(agent, model_ref=model_ref_value, provider_id=provider_id),
        ]:
            case["classification"] = _classify_result(case)
            provider_report["cases"].append(case)
            if case["classification"] == "unexpected_error":
                unexpected.append(f"{provider_id}/{case['label']}: {case.get('errors')}")
        report["providers"].append(provider_report)

    path = _report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    assert candidates, "No configured provider candidates found for live provider matrix"
    assert not unexpected, f"Unexpected live provider failures; see {path}: {unexpected}"
