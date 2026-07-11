from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


pytestmark = pytest.mark.live_provider


OUT_OF_CREDITS_RE = re.compile(
    r"out of (?:prepaid )?credits|credit(?:s)? (?:are )?(?:exhausted|depleted)|"
    r"usage credits? (?:are )?(?:exhausted|depleted)|insufficient (?:balance|credits)|"
    r"payment required|account balance.{0,80}(?:exhausted|depleted|insufficient|too low)|"
    r"(?:billing|spend) limit (?:reached|exceeded)|"
    r"(?:quota|usage).{0,120}(?:billing|plan)|(?:billing|plan).{0,120}(?:quota|usage)",
    re.IGNORECASE,
)


FALLBACK_LIVE_MODEL_IDS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "atlascloud": "anthropic/claude-opus-4.8",
    "claude_subscription": "claude-sonnet-4-6",
    "codex": "gpt-5.5",
    "google": "gemini-3.1-flash-lite-preview",
    "minimax": "MiniMax-M2.7",
    "ollama_cloud": "gpt-oss:120b-cloud",
    "openai": "gpt-5.4",
    "opencode_go": "qwen3.7-max",
    "opencode_zen": "kimi-k2.6",
    "openrouter": "z-ai/glm-5.2",
    "xai": "grok-4-1-fast-reasoning",
    "xai_oauth": "grok-4.20-0309-non-reasoning",
}


def _live_data_dir() -> Path:
    return Path(os.environ.get("ROW_BOT_LIVE_PROVIDER_DATA_DIR") or Path.home() / ".row-bot").expanduser()


def _enabled() -> bool:
    return os.environ.get("ROW_BOT_LIVE_PROVIDER_E2E", "").strip().lower() in {"1", "true", "yes"}


def _report_path() -> Path:
    return Path(os.environ.get("ROW_BOT_LIVE_PROVIDER_REPORT", "test-results/live_provider_matrix.json"))


def _agent_smoke_candidate_supported(provider_id: str, snapshot: Any) -> bool:
    if provider_id != "openrouter":
        return True
    return isinstance(snapshot, dict) and snapshot.get("tool_calling") is True


def _hydrate_live_provider_profile() -> dict[str, Any]:
    from row_bot import api_keys, secret_store
    from row_bot.data_paths import get_row_bot_data_dir
    from row_bot.providers import config as provider_config
    from row_bot.providers.auth_store import PROVIDER_API_KEY_ENV

    live_dir = _live_data_dir()
    test_data_dir = get_row_bot_data_dir()
    live_service = secret_store.service_name_for(live_dir)
    live_provider_config = provider_config.load_provider_config(live_dir / "providers.json")
    provider_config.save_provider_config(live_provider_config)
    copied_cache_files: list[str] = []
    for filename in ("cloud_models_cache.json", "context_catalog_cache.json", "model_catalog_cache.json"):
        source = live_dir / filename
        if not source.exists():
            continue
        destination = test_data_dir / filename
        if source.resolve() == destination.resolve():
            continue
        shutil.copyfile(source, destination)
        copied_cache_files.append(filename)
    loaded_env_keys: list[str] = []
    for env_var in sorted(set(PROVIDER_API_KEY_ENV.values())):
        value = api_keys.get_key_for_data_dir(live_dir, env_var)
        if not value:
            continue
        os.environ[env_var] = value
        loaded_env_keys.append(env_var)
    secret_store.SERVICE_NAME = live_service
    return {
        "loaded_api_key_envs": loaded_env_keys,
        "copied_cache_files": copied_cache_files,
        "copied_quick_choices": len(live_provider_config.get("quick_choices") or []),
        "copied_custom_endpoints": len(live_provider_config.get("custom_endpoints") or []),
    }


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


def _run_agent_case(
    agent_module,
    *,
    model_ref: str,
    provider_id: str,
    prompt: str,
    tools: list[str],
    label: str,
    requires_tool_round_trip: bool = False,
) -> dict[str, Any]:
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
            "requires_tool_round_trip": requires_tool_round_trip,
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
            "requires_tool_round_trip": requires_tool_round_trip,
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
    from row_bot.threads import get_latest_checkpoint_messages

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
    diagnostics_before = agent_module._provider_transcript_diagnostics(get_latest_checkpoint_messages(thread_id))
    diagnostics_after = agent_module._provider_transcript_diagnostics(
        agent_module._normalize_provider_facing_messages(
            get_latest_checkpoint_messages(thread_id),
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
        if provider_id == "openrouter" and model_id != FALLBACK_LIVE_MODEL_IDS["openrouter"]:
            continue
        snapshot = choice.get("capabilities_snapshot") if isinstance(choice.get("capabilities_snapshot"), dict) else {}
        if not _agent_smoke_candidate_supported(provider_id, snapshot):
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

    for provider_id in sorted(configured):
        if provider_id in candidates:
            continue
        model_id = FALLBACK_LIVE_MODEL_IDS.get(provider_id)
        if not model_id:
            continue
        status = provider_status(provider_id)
        if not status.get("configured"):
            continue
        if provider_id == "openrouter":
            from row_bot.models import _cloud_model_cache

            cached = _cloud_model_cache.get(model_ref(provider_id, model_id)) or _cloud_model_cache.get(model_id)
            snapshot = cached.get("capabilities_snapshot") if isinstance(cached, dict) and isinstance(cached.get("capabilities_snapshot"), dict) else {}
            if not _agent_smoke_candidate_supported(provider_id, snapshot):
                continue
        candidates[provider_id] = {
            "provider_id": provider_id,
            "model_ref": model_ref(provider_id, model_id),
            "model_id": model_id,
            "display_name": model_id,
        }

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
        candidates[provider_id] = {
            "provider_id": provider_id,
            "model_ref": model_ref(provider_id, model_id) if model_id else "",
            "model_id": model_id,
            "display_name": str(endpoint.get("display_name") or endpoint.get("name") or model_id or provider_id),
            **({"configuration_error": "configured custom endpoint has no model"} if not model_id else {}),
        }

    for provider_id in sorted(configured - set(candidates)):
        candidates[provider_id] = {
            "provider_id": provider_id,
            "model_ref": "",
            "model_id": "",
            "display_name": provider_id,
            "configuration_error": "configured provider has no discoverable Agent model",
        }

    return sorted(candidates.values(), key=lambda item: item["provider_id"])


def _classify_result(case: dict[str, Any]) -> str:
    if case.get("status") == "done" and case.get("answer_chars", 0) > 0:
        if case.get("requires_tool_round_trip") and (
            case.get("tool_calls", 0) < 1 or case.get("tool_results", 0) < 1
        ):
            return "unexpected_error"
        return "pass"
    text = " ".join(str(error) for error in case.get("errors") or [])
    if OUT_OF_CREDITS_RE.search(text):
        return "acceptable_error"
    return "unexpected_error"


@pytest.mark.skipif(not _enabled(), reason="set ROW_BOT_LIVE_PROVIDER_E2E=1 to run real provider calls")
def test_live_configured_provider_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))
    os.environ.pop("ROW_BOT_TEST_MODE", None)
    profile = _hydrate_live_provider_profile()
    import row_bot.agent as agent
    from row_bot.tools import registry as tool_registry

    candidates = _discover_live_candidates()
    default_tool_names = [tool.name for tool in tool_registry.get_enabled_tools()]
    gmail_tool = tool_registry.get_tool("gmail")
    if gmail_tool is not None:
        # Bind the authenticated-only Gmail declarations without loading or
        # calling a real mailbox. The prompt below calls row_bot_status only.
        monkeypatch.setattr(gmail_tool, "has_credentials_file", lambda: True)
        monkeypatch.setattr(gmail_tool, "is_authenticated", lambda: True)
        monkeypatch.setattr(gmail_tool, "_build_api_resource", lambda: object())
        monkeypatch.setattr(
            gmail_tool,
            "_get_selected_operations",
            lambda: ["create_gmail_draft", "send_gmail_message"],
        )
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profile": profile,
        "candidates": candidates,
        "providers": [],
    }
    unexpected: list[str] = []

    for candidate in candidates:
        provider_id = candidate["provider_id"]
        model_ref_value = candidate["model_ref"]
        provider_report = dict(candidate)
        provider_report["cases"] = []
        configuration_error = str(candidate.get("configuration_error") or "")
        if configuration_error:
            case = {
                "label": "provider_discovery",
                "status": "configuration_error",
                "answer_chars": 0,
                "errors": [configuration_error],
                "tool_calls": 0,
                "tool_results": 0,
                "thinking_chars": 0,
                "event_types": [],
            }
            case["classification"] = _classify_result(case)
            provider_report["cases"].append(case)
            unexpected.append(f"{provider_id}/{case['label']}: {case.get('errors')}")
            report["providers"].append(provider_report)
            continue
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
                label="status_tool_round_trip",
                requires_tool_round_trip=True,
            ),
            _run_agent_case(
                agent,
                model_ref=model_ref_value,
                provider_id=provider_id,
                prompt=(
                    "Use row_bot_status with category tools, then answer with only the enabled "
                    "and disabled tool counts. Do not call goal_update."
                ),
                tools=["goal", "row_bot_status"],
                label="goal_status_mixed_bundle",
                requires_tool_round_trip=True,
            ),
            _run_agent_case(
                agent,
                model_ref=model_ref_value,
                provider_id=provider_id,
                prompt=(
                    "Use row_bot_status with category tools, then answer with only the enabled "
                    "and disabled tool counts. Do not create a draft or send email."
                ),
                tools=["gmail", "row_bot_status"],
                label="gmail_status_schema_bundle",
                requires_tool_round_trip=True,
            ),
            _run_agent_case(
                agent,
                model_ref=model_ref_value,
                provider_id=provider_id,
                prompt=(
                    "Use row_bot_status with category tools, then answer with only the enabled "
                    "and disabled tool counts. Do not use any mutating tool."
                ),
                tools=default_tool_names,
                label="default_application_tool_bundle",
                requires_tool_round_trip=True,
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
