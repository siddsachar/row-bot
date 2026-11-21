"""A model on this computer gets the local middle tier (local-models uplift L2).

Up to 128K, a local model's rarely needed groups (Row-Bot's self-management) are offered through
tool search: its whole prompt is processed here, so every token costs time to the first word. A
custom endpoint saved as local (Run on this computer, a found server, one added by hand) now counts,
as Ollama always did; a remote one doesn't, and Learning's personal models keep the tier they were
trained in (``learning/training/shape.py``). The real tool assembly runs over saved endpoints and the
real context policy; nothing reaches a provider.
"""

from __future__ import annotations

from contextvars import ContextVar
from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool

import row_bot.models as models
import row_bot.providers.config as provider_config
from row_bot.providers.custom import custom_provider_id, save_custom_endpoint

pytestmark = pytest.mark.subsystem

ALWAYS = {"read_tool_output", "ask_person"}
# The middle tier: Row-Bot's self-management is offered through tool search, not bound.
MIDDLE_TIER = {"search_memory", "tool_search", "tool_invoke", *ALWAYS}
FULL = {"row_bot_status", "search_memory", *ALWAYS}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    import row_bot.agent as agent

    variables = {value for value in vars(agent).values() if isinstance(value, ContextVar)}
    tokens = [(variable, variable.set(variable.get(None))) for variable in variables]
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(models, "_cloud_context_override", None)
    agent.clear_agent_cache()
    agent._approval_mode_var.set("auto")
    monkeypatch.setattr(agent, "_build_runtime_skill_snapshot", lambda: ((), (), False, "test-skills"))
    monkeypatch.setattr(agent, "get_llm", lambda: object())
    monkeypatch.setattr(agent, "get_agent_system_prompt", lambda: "system")
    monkeypatch.setattr(agent, "_ensure_agent_mode_ready", lambda model_name: SimpleNamespace(
        provider_id="test", runtime_model="test", capability_source="test", confidence="high"))
    monkeypatch.setattr(agent, "create_react_agent", lambda **kwargs: SimpleNamespace(**kwargs))
    tools = {"row_bot_status": [_tool("row_bot_status")], "memory": [_tool("search_memory")]}
    monkeypatch.setattr(agent.tool_registry, "get_tool", lambda name: SimpleNamespace(
        as_langchain_tools=lambda: tools[name], destructive_tool_names=set()) if name in tools else None)
    try:
        yield agent
    finally:
        agent.clear_agent_cache()
        for variable, token in reversed(tokens):
            variable.reset(token)


def _tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(func=lambda query="": f"{name}:{query}", name=name, description=f"{name} tool")


def _bound_on(agent, monkeypatch, endpoint_id: str, window: int, **endpoint) -> set[str]:
    save_custom_endpoint({
        "id": endpoint_id, "name": endpoint_id, "base_url": "http://127.0.0.1:8000/v1", "auth_required": False,
        "models": [{"id": "qwen", "model_id": "qwen", "ctx": window, "provider": custom_provider_id(endpoint_id),
                    "capabilities_snapshot": {"tasks": ["chat"]}}],
        **endpoint,
    })
    ref = f"model:{custom_provider_id(endpoint_id)}:qwen"
    assert models.get_context_size(ref) == window
    monkeypatch.setattr(agent, "get_current_model", lambda: ref)
    agent.clear_agent_cache()
    graph = agent.get_agent_graph(["row_bot_status", "memory"])
    return set(graph.tools.tools_by_name)


def test_a_local_endpoint_at_96k_gets_the_local_middle_tier(agent, monkeypatch):
    assert _bound_on(agent, monkeypatch, "my-llama", 98_304, profile="llama_cpp", execution_location="local") == MIDDLE_TIER


def test_run_on_this_computer_at_96k_gets_the_local_middle_tier(agent, monkeypatch):
    bound = _bound_on(agent, monkeypatch, "llamacpp", 98_304, profile="llama_cpp", execution_location="local",
                      managed="local", manual_capabilities={"context_window": 98_304})

    assert bound == MIDDLE_TIER


def test_a_remote_endpoint_at_96k_keeps_the_full_tool_set(agent, monkeypatch):
    assert _bound_on(agent, monkeypatch, "proxy", 98_304, execution_location="remote") == FULL


@pytest.mark.parametrize("window, expected", [(65_536, MIDDLE_TIER), (98_304, FULL)])
@pytest.mark.parametrize("endpoint_id, endpoint", [
    ("row-bot-personal-row-bot", {"profile": "llama_cpp", "managed": "learning", "reasoning_mode": "off"}),
    # The endpoint Learning registers on LM Studio, and the one its evaluation worker is handed (no "managed").
    ("row-bot-personal-lmstudio", {"profile": "lmstudio"}),
])
def test_learnings_personal_models_keep_the_tier_they_were_trained_in(agent, monkeypatch, window, expected,
                                                                       endpoint_id, endpoint):
    """Learning's constraint (L2): the tier a personal model is served in is the one shape.py trained it in,
    the same as a remote endpoint's: the middle tier up to 64K, every group above it."""
    personal = _bound_on(agent, monkeypatch, endpoint_id, window, execution_location="local", **endpoint)
    personal_parents = set(agent._current_effective_tool_parent_names_var.get())
    remote = _bound_on(agent, monkeypatch, "proxy", window, execution_location="remote")

    assert personal == remote == expected
    assert personal_parents == set(agent._current_effective_tool_parent_names_var.get())
