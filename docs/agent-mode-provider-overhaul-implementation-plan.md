# Agent Mode Provider Overhaul Implementation Plan

## Status

Implementation completed on `codex/local-provider-compatibility`; targeted verification has passed.

Completed so far:

- Added `providers.readiness.AgentReadinessResult` and Agent Mode readiness evaluation.
- Enforced the 32k Agent Mode context gate.
- Added OpenRouter fail-closed readiness behavior for missing/inconclusive tool metadata.
- Added custom OpenAI-compatible readiness blocking unless probes prove structured tool calling and tool-result round trip.
- Removed normal assistant graph plain-chat fallback branches from `agent.py`.
- Kept the ReAct graph on the full Agent Mode prompt and enabled tool set.
- Upgraded custom endpoint probes to check structured tool calls and tool-result continuation.
- Updated custom endpoint profiles/transports so Agent Mode does not flatten or drop tool history.
- Added model catalog blocked state for Agent-incompatible chat models while keeping them visible.
- Added focused tests covering readiness, custom probes, transport tool history, cloud runtime constructors, and catalog blocked state.

Verification:

- `pytest tests/test_agent_readiness.py tests/test_provider_runtime.py tests/test_provider_custom.py tests/test_openai_compatible_transport.py tests/test_provider_catalog.py tests/test_provider_resolution.py tests/test_provider_selection.py tests/test_context_policy.py tests/test_context_override_runtime.py tests/test_model_picker_regressions.py tests/test_mcp_client.py -q --basetemp .tmp/pytest`
- `python -m py_compile providers/readiness.py providers/catalog.py providers/custom.py providers/model_catalog.py providers/transports/openai_compatible.py agent.py`

This plan supersedes the earlier plain-chat compatibility direction for Thoth's main assistant runtime. Thoth should have one supported interactive assistant mode: full Agent Mode. Custom and local providers are supported only when they satisfy the Agent Mode contract.

## Decisions

- Agent Mode only for interactive assistant chat.
- No plain-chat fallback.
- No auxiliary-mode work in this overhaul.
- Minimum context for Agent Mode: 32,000 tokens.
- OpenRouter unknown tool metadata should fail closed unless metadata or a curated override confirms tool support.
- Basic UX only for now; advanced/developer model override mode is deferred.
- Incompatible models should remain visible with a blocked state rather than being hidden.
- Do not break existing cloud provider constructors or media/provider flows.

## Goal

Make Thoth's provider layer robust enough for cloud, local, and custom endpoints without changing Thoth's core behavior. Thoth is a full personal assistant with tools, memory, skills, long prompts, and ReAct-style execution. Provider compatibility must be enforced through explicit capability contracts, not by silently downgrading the assistant.

The desired result is:

- Known cloud chat providers continue working as they do today.
- Local/custom providers can become usable after successful capability probing.
- Non-agent-ready models are clearly blocked before agent startup.
- Provider quirks live in provider profiles and transport adapters, not in broad agent prompt changes.
- The main agent graph always receives the full tool set for compatible providers.

## Hermes-Informed Architecture

Hermes patterns to adapt:

- Shared provider runtime resolution used by all runtime entry points.
- Provider/model/runtime selection as a first-class contract, not only a model string.
- Provider registry plus profiles/overlays instead of LiteLLM as the internal abstraction.
- Explicit context and tool-capability gates for agent workflows.
- Local endpoint probing for runtime context and model metadata.
- Transport adapters that own request/response conversion while leaving the agent loop stable.
- User-visible constraints and actionable errors for non-agentic models.

Hermes patterns to avoid or not copy directly:

- Do not rewrite Thoth's agent loop or prompt strategy around provider compatibility.
- Do not use LiteLLM as the central in-process abstraction. LiteLLM can remain a supported OpenAI-compatible gateway profile later.
- Do not silently route unknown models to OpenRouter or cloud fallback.
- Do not assume OpenAI-compatible HTTP shape means tool support.
- Do not cache LM Studio runtime context as if it were permanently tied to the model name.

Primary Hermes sources reviewed:

- `website/docs/developer-guide/provider-runtime.md`
- `website/docs/developer-guide/adding-providers.md`
- `hermes_cli/runtime_provider.py`
- `hermes_cli/providers.py`
- `hermes_cli/model_switch.py`
- `agent/model_metadata.py`
- `agent/agent_init.py`
- `agent/conversation_loop.py`
- `agent/conversation_compression.py`
- `agent/transports/base.py`
- `agent/transports/chat_completions.py`
- `agent/models_dev.py`

## Current Thoth Architecture To Preserve

Current branch work already has useful pieces:

- `providers.models.ProviderDefinition`
- `providers.models.ModelInfo`
- `providers.models.RoutingProfile`
- `providers.catalog.PROVIDER_DEFINITIONS`
- `providers.capabilities`
- `providers.resolution.resolve_provider_config`
- `providers.runtime.create_chat_model`
- `providers.custom` endpoint profiles and probes
- `providers.transports.openai_compatible.ChatOpenAICompatible`
- `models.get_context_policy`
- Provider-qualified model refs such as `model:<provider>:<model>`

These should be evolved rather than replaced wholesale.

## Cloud Compatibility Requirements

Do not break existing cloud providers.

The existing provider-specific constructors in `providers/runtime.py` should stay intact:

- OpenAI: `ChatOpenAI`, with Responses API enabled for selected models.
- Anthropic: `ChatAnthropic`.
- Google: `ChatGoogleGenerativeAI`.
- xAI: `ChatXAI`.
- MiniMax: Anthropic-compatible `ChatAnthropic`.
- OpenRouter: `ChatOpenRouter`.
- Codex: `ChatCodexResponses`.
- Ollama Cloud: `ChatOllamaCloud`.

The new readiness gate must not force these providers through the custom OpenAI-compatible transport.

Known cloud providers should pass Agent Mode readiness from trusted catalog/runtime evidence and curated provider rules. They should not require live probes during normal selection or startup.

Media, image, video, embedding, and other non-chat provider flows should not be modified as part of this overhaul except where catalog filtering needs to prevent those models from being chosen for Agent Mode.

## Agent Mode Contract

A model is Agent-ready only if all required conditions are satisfied:

- It supports chat or responses-style message generation.
- It produces text output.
- It supports structured tool calling.
- It supports tool-result round trips.
- It has an effective context window of at least 32,000 tokens.
- Its transport can represent Thoth's system, user, assistant, tool-call, and tool-result messages.
- Its provider credentials/runtime are configured.
- Its provider profile has no known blocker for Agent Mode.

For Agent Mode, unknown tool support must not pass.

For known cloud providers, tool support can be proven by trusted catalog/provider rules. For custom/local OpenAI-compatible endpoints, tool support must be proven by a successful probe.

## New Readiness Model

Add an `AgentReadinessResult` contract, likely under `providers/capabilities.py` or a new `providers/readiness.py`.

Suggested fields:

- `ready: bool`
- `provider_id: str`
- `model_id: str`
- `runtime_model: str`
- `selection_ref: str`
- `transport: TransportMode`
- `context_window: int | None`
- `required_context: int`
- `tool_calling: bool | None`
- `tool_calling_source: str`
- `tool_round_trip: bool | None`
- `streaming: bool | None`
- `credential_status: str`
- `capability_source: str`
- `confidence: str`
- `errors: list[str]`
- `warnings: list[str]`
- `actions: list[str]`

Readiness should be computed from `ResolvedProviderConfig`, model metadata, context policy, provider profile, and probe results.

## Capability Evidence

Upgrade capability facts from simple booleans where needed.

For important agent capabilities, record:

- value: `true`, `false`, or `unknown`
- source: catalog, provider metadata, probe, profile, curated rule, user context override
- confidence: high, medium, low
- last verified timestamp when probe-based
- failure reason when known

Initial implementation can keep existing `ModelInfo` fields and add normalized evidence helpers rather than deeply refactoring every catalog object at once.

## Provider Classes

Provider groups should have different evidence policies.

Trusted cloud providers:

- OpenAI
- Anthropic
- Google
- xAI
- MiniMax
- Codex
- Ollama Cloud

Use catalog/provider rules and fetched metadata. Do not require live probes.

OpenRouter:

- Use OpenRouter metadata when available.
- If `supported_parameters` exists, require `tools` or `tool_choice`.
- If tool metadata is missing, fail closed unless a curated override confirms Agent Mode support.

Ollama local:

- Use existing local model/tool heuristics where available.
- Preserve current strict tool-call requirement for the agent.
- Enforce 32k effective context.

Custom/local OpenAI-compatible endpoints:

- Default tool support to unknown.
- Require successful probe for Agent Mode.
- Never flatten tool history for Agent Mode.
- Never omit tools to make a non-tool model respond.

## Runtime Resolution

Strengthen `providers/resolution.py` so one resolved object is the source of truth for runtime and readiness.

`ResolvedProviderConfig` should include or expose:

- canonical provider ID
- canonical model ID
- runtime model string
- selection ref
- transport mode
- API mode if separate from transport
- base URL
- auth source
- endpoint profile
- execution location
- risk label
- context policy
- capability snapshot
- probe metadata
- compatibility warnings/errors

Provider inference must not silently convert custom endpoints or unknown hosts into unrelated providers. API keys must remain host/provider scoped.

## Agent Startup Gate

Add a single Agent Mode preflight before graph construction in `agent.py`.

Preflight steps:

1. Resolve active model through provider resolution.
2. Compute context policy.
3. Compute Agent readiness.
4. If not ready, raise a user-facing compatibility error before building tools or creating the graph.
5. If ready, continue with the existing ReAct/tool graph and full tool set.

Example error:

`qwen2.5-7b via LM Studio is not Agent-ready: tool calling was not detected. Thoth requires tool calling for memory, skills, and actions. Re-probe the endpoint or select an Agent-ready model.`

The graph cache key should include provider/model/context/readiness-relevant identity, but not a plain-chat mode flag.

## Remove Plain-Chat Fallback

Remove or stop using the current plain-chat compatibility path in `agent.py`:

- `_custom_endpoint_prefers_plain_chat`
- `_active_custom_endpoint_prefers_plain_chat`
- `_pre_model_trim_plain_chat`
- any branch that empties `lc_tools`
- any branch that suppresses plugin/channel tools for custom endpoints
- any branch that swaps in `get_plain_chat_system_prompt`
- any cache key dimension for `plain_custom`

`get_plain_chat_system_prompt` can be left temporarily if other imports/tests still reference it, but normal assistant chat must not use it.

## Custom Endpoint Probe

Upgrade `providers/custom.py` probing for custom/local OpenAI-compatible endpoints.

Probe sequence:

1. Normalize base URL and profile.
2. Fetch `/models` when available.
3. Run a simple chat request.
4. Run a structured tool-call request with a harmless test tool.
5. Verify the response contains a parseable structured tool call.
6. Send a tool-result continuation and verify the endpoint accepts the round trip.
7. Run a streaming probe and verify usable deltas or record non-stream fallback.
8. Extract context metadata from model fields.
9. Fingerprint server type where possible.
10. Store evidence and errors in endpoint `last_probe`.

Tool-call probe should not accept a plain-text answer that merely describes calling a tool. It must verify provider-native structured tool-call output.

## Context Detection

Use `models.get_context_policy` as the center of context behavior.

Context sources, from strongest to weakest:

- explicit provider metadata
- OpenRouter model metadata
- known cloud catalog/rules
- custom endpoint `/models` context fields
- LM Studio native `/api/v1/models`
- llama.cpp `/props`
- vLLM metadata where available
- Ollama metadata
- user context override
- heuristic fallback

For Agent Mode, the effective context must be at least 32k.

For custom/local endpoints, heuristic fallback should produce a warning. It should not make a model Agent-ready unless the endpoint also has proven tool support.

LM Studio context should be treated as runtime-specific and should not be persistently trusted across loaded model changes without revalidation.

## Transport Adapter Responsibilities

Keep provider quirks in transport/profile code, not the agent prompt.

Transport adapters should own:

- message conversion
- system message handling
- assistant tool-call formatting
- tool-result message formatting
- tool schema conversion
- request parameter normalization
- unsupported parameter dropping
- streaming delta parsing
- tool-call chunk parsing
- reasoning field normalization
- provider-specific retry/fallback behavior

Transport adapters should not decide to downgrade Agent Mode. They either satisfy the required message/tool contract or report incompatibility.

## UI Requirements

Basic UX for now.

Model/provider UI should show incompatible models with blocked state. Do not hide them.

Readiness states:

- Agent-ready
- Probe required
- Tool calling not detected
- Tool round trip failed
- Context too small
- Credentials missing
- Endpoint unreachable
- Streaming unsupported, non-stream fallback available
- Manual context override active
- Blocked for Agent Mode

For blocked models, show concise remediation:

- choose another model
- configure credentials
- increase context
- re-probe endpoint
- inspect endpoint profile

Advanced/developer override UX is deferred.

## OpenRouter Policy

OpenRouter should be stricter than direct trusted providers.

Rules:

- If OpenRouter metadata includes `supported_parameters`, require `tools` or `tool_choice`.
- If metadata explicitly lacks tool support, block.
- If metadata is missing or inconclusive, block unless a curated allowlist entry confirms tool support.
- Keep the model visible with a blocked state.

This avoids treating a routed model as Agent-ready merely because it can chat.

## Testing Plan

Add or update tests for:

- OpenAI constructor path remains unchanged.
- Anthropic constructor path remains unchanged.
- Google constructor path remains unchanged.
- xAI constructor path remains unchanged.
- MiniMax constructor path remains unchanged.
- OpenRouter constructor path remains unchanged.
- Codex constructor path remains unchanged.
- Ollama Cloud constructor path remains unchanged.
- Known cloud chat models pass readiness without live probe.
- Image/video/embedding models are not Agent-ready.
- OpenRouter models without tool metadata fail closed.
- Custom endpoint with unknown tool support is blocked.
- Custom endpoint with failed tool probe is blocked.
- Custom endpoint with successful tool-call and tool-result probe can pass readiness.
- Custom endpoint never flattens tools for Agent Mode.
- Agent graph receives full tools for Agent-ready providers.
- Agent graph is not built when readiness fails.
- Effective context below 32k blocks Agent Mode.
- Manual context override can satisfy context but not fake tool support.
- LM Studio context metadata is revalidated when runtime/model changes.
- API keys are not leaked to unrelated custom hosts.

## Migration Steps

1. Add readiness contract and tests around the current cloud providers.
2. Implement Agent readiness evaluation using existing catalog/context data.
3. Add the Agent startup preflight while leaving current constructors intact.
4. Remove the plain-chat fallback path from normal agent graph creation.
5. Replace tests that expected custom plain-chat fallback with readiness-blocking tests.
6. Upgrade custom endpoint probes for structured tool calls and tool-result round trips.
7. Update custom endpoint profiles so tool support defaults to unknown unless proven.
8. Add OpenRouter fail-closed policy for unknown tool metadata.
9. Update model picker/provider UI with blocked states and remediation text.
10. Run targeted provider/runtime/agent tests.
11. Run broader suite, noting optional dependency gaps separately.
12. Update docs/release notes after behavior is verified.

## Acceptance Criteria

The overhaul is complete when:

- Thoth has one interactive assistant runtime: full Agent Mode.
- No local/custom provider silently downgrades to plain chat.
- No provider path silently drops tools.
- Known cloud providers continue working through their existing constructors.
- Incompatible models are visible but blocked with actionable explanations.
- Custom/local endpoints become Agent-ready only after proof of structured tool calling and sufficient context.
- OpenRouter unknown tool support fails closed.
- Context below 32k blocks Agent Mode.
- Provider-specific fixes live in profiles/transports/readiness code, not broad prompt changes.
- Existing Thoth agent prompt and ReAct behavior remain stable.

## Starting Prompt For Implementation Thread

Use this prompt in a new thread:

```text
We are working in D:\Code\Thoth on branch codex/local-provider-compatibility.

Implement the Agent Mode provider overhaul described in docs/agent-mode-provider-overhaul-implementation-plan.md.

Important constraints:
- Do not introduce plain-chat mode or auxiliary modes.
- Remove the current automatic plain-chat fallback for custom endpoints.
- Thoth's only interactive assistant runtime should be full Agent Mode.
- Agent Mode minimum context is 32k.
- Known cloud providers must not break and should keep their existing runtime constructors.
- Known cloud providers may pass readiness from trusted catalog/provider evidence without live probes.
- OpenRouter should fail closed when tool metadata is missing or inconclusive, unless a curated override confirms tool support.
- Custom/local OpenAI-compatible endpoints must prove structured tool calling and tool-result round trip through probes before becoming Agent-ready.
- Incompatible models should remain visible with a blocked state.
- Keep broad prompt and agent behavior stable; provider quirks belong in provider profiles, readiness checks, and transports.

Start by reading:
- docs/agent-mode-provider-overhaul-implementation-plan.md
- providers/models.py
- providers/catalog.py
- providers/capabilities.py
- providers/resolution.py
- providers/runtime.py
- providers/custom.py
- providers/transports/openai_compatible.py
- models.py
- agent.py
- tests around provider catalog/runtime/custom endpoints/agent graph

Then implement end to end with focused tests first, preserving existing cloud provider behavior.
```
