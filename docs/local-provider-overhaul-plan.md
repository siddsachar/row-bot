# Local Provider Compatibility Overhaul

> Superseded note: the Agent Mode provider overhaul removes the automatic
> plain-chat fallback described in this earlier plan. Thoth's interactive
> assistant runtime is now full Agent Mode only; custom/local endpoints must
> prove native structured tool calling and tool-result round trips before they
> are Agent-ready.

## Goal

Make local and custom providers first-class routes instead of treating them as loose model-name strings. Thoth should preserve provider identity, use provider-native transports, handle OpenAI-compatible custom endpoints with profile-specific compatibility behavior, and apply context limits consistently across cloud, local, and custom models.

## Implementation Status

- Provider identity now preserves canonical `model:<provider>:<model>` selections instead of falling back to OpenRouter for unknown bare local names.
- Runtime resolution now routes through provider config resolution and supports custom OpenAI-compatible endpoints through a native transport path.
- Custom endpoint profiles and probing support oMLX, vLLM, llama.cpp, LM Studio, Ollama OpenAI-compatible, and generic OpenAI-compatible servers.
- Context policy now centralizes provider/native/user-cap decisions for cloud, local, and custom models.
- Setup, provider settings, chat picker, and model settings have been updated to work with canonical provider refs and context policy.
- Models settings performance hardening is implemented for initial render: the tab now renders from a precomputed snapshot and avoids provider IO on the event loop during the first UI build.

## Models Settings Crash Requirement

The Models settings page must not call provider discovery, Ollama, custom endpoint probing, model-context probing, or media-model catalog helpers during initial UI render. Data collection may do that work through `run.io_bound`, then render should consume only the collected snapshot.

Required behavior:

- Settings -> Models opens quickly even when Ollama is stopped, slow, or unreachable.
- Context policy is precomputed off the event loop and cached in the render snapshot.
- Image/video model options are precomputed off the event loop.
- Download/local-state checks during initial render use the preloaded local model set.
- Explicit user actions, such as changing a model or downloading one, may perform provider work, but through async/threaded calls.
- Catalog browsing remains lazy and cache-first.

Implemented details:

- `_collect_models_tab_data()` now collects current chat/vision selections, local model state, context policy, and image/video picker options off the event loop.
- `_render_models_tab_content()` consumes the snapshot directly for initial context visibility, media picker options, source badges, and download/missing-model visibility.
- Initial render uses a preloaded local model set instead of calling `is_model_local()`, `list_local_models()`, Ollama reachability checks, or context probing.
- Download actions refresh Ollama reachability/local model state through `run.io_bound`.
- Context note updates use the cached policy and refresh policy asynchronously after explicit model changes.

## Remaining Validation

- Automated tests verify the initial render path is snapshot-only and existing provider/catalog/context tests stay green.
- Manual Settings/Models validation for Ollama stopped, Ollama running with local models, custom endpoint offline, pin/unpin/default flows, chat picker override/default, and local Vision has been completed by the user.
- Live oMLX/macOS custom endpoint validation is deferred until this branch is copied to a Mac test environment.
- Remaining in-repo work before shipping: run the deferred live oMLX/macOS validation on Apple Silicon with oMLX serving a non-instruct model.

## Context Override Follow-Up

Implemented:

- Agent graph cache keys now use `get_context_size(active_model)` instead of the global default context. A thread override such as `model:ollama:qwen3:14b` now gets its own context-specific graph cache entry.
- Default Ollama client creation now applies the effective capped context, so choosing a context larger than the model's native window falls back to the supported max instead of passing the oversized value to Ollama.
- Changing either local or cloud context settings clears cached override LLM clients, so thread/model overrides cannot keep stale context settings.
- Custom OpenAI-compatible endpoint profiles now distinguish trim-only behavior from request-time context parameters. Profiles such as llama.cpp that declare a `context_param_name` receive the effective capped context in the request body.
- The generic/oMLX/LM Studio style custom profiles remain trim-only when the endpoint does not support request-time context changes.

## Non-Tool Model Policy

Implemented policy:

- Native Ollama agent chat still requires tool-call support because Thoth's primary chat runtime is a ReAct/tool graph. The chat picker and Settings default-model flow check local Ollama tool support before accepting a model and fail early with the active model name.
- OpenAI-compatible custom endpoints use profile-aware compatibility instead of assuming native tools. oMLX and LM Studio style profiles flatten prior tool history and omit unsupported tool parameters so non-tool chat templates receive normal user/assistant-shaped messages.
- Non-tool custom endpoint support is best-effort plain assistant response inside the agent graph; autonomous tool use requires a model/profile that accepts tool calls.
- Vision/media local models are independent of chat tool support and preserve provider refs in settings while stripping them at the Ollama runtime edge.

## Broad Suite Investigation

Findings:

- The old broad collection timeout was not reproduced after isolating the legacy harness. `tests/test_suite.py` is a monolithic import-time contract script; pytest reports no collected tests from it after about 8.5 seconds.
- `tests/test_memory_e2e.py` changed process cwd to the `tests` directory at import time, which made later pytest source-contract tests fail to find repo-root files. This was fixed to chdir to the actual repo root.
- The normal pytest suite excluding `tests/test_suite.py` now runs to completion in this environment. Remaining broad-suite failures are environmental optional dependency gaps, not provider overhaul failures: `telegram` and `langchain_community` are missing.

## Docs And Release Polish

Implemented:

- README quick-start copy now names oMLX, LM Studio, vLLM, llama.cpp, and custom compatibility profiles.
- Architecture docs now describe provider-qualified model refs, custom endpoint profiles, and context override/cache behavior.
- Release notes now include provider identity, custom endpoint compatibility, context override consistency, and non-tool model guardrails.

## Manual Windows LM Studio Follow-Up

Manual testing found two local custom endpoint regressions:

- The chat privacy banner treated a local LM Studio endpoint as cloud because it used `is_cloud_model()` for all provider-backed refs. The banner now resolves provider config and uses `execution_location`/`risk_label`, so local custom endpoints show local/private copy.
- LM Studio/Qwen can stream or return reasoning in `reasoning_content` while `content` is empty during the thinking phase. The OpenAI-compatible custom transport now preserves reasoning content in `additional_kwargs`, streams reasoning chunks for Thoth's thinking UI, and logs custom stream starts/completions or contentless completions.
- If a custom endpoint probe records `streaming_ok: false`, the OpenAI-compatible custom transport now falls back to a non-stream request while still yielding the final response into Thoth's streaming UI. This fixes LM Studio turns that ended instantly with no visible assistant response.
- The custom endpoint probe now actually tests streaming before setting `streaming_ok`; it no longer records `false` by default when streaming was never checked. Live LM Studio streaming was verified to emit reasoning chunks and final content.
- Custom endpoint probe failures now log structured warnings and show details in the UI notification/tooltip instead of only a generic `probe failed` badge.

End-to-end LM Studio investigation update:

- Direct `/v1/chat/completions` and the native `ChatOpenAICompatible` transport worked with a simple user message, but the full app graph could produce an empty SSE stream with no exception. Runtime logs showed `custom_openai_stream: completed without content`, then the UI ended the turn.
- Reproducing the graph path in an isolated data directory exposed the hidden failure behind the empty stream: LM Studio rejected Thoth's full agent prompt for the loaded 4K context (`n_keep` greater than `n_ctx`). The streaming endpoint could end with only `[DONE]`, so the app never surfaced the context error before the fallback existed.
- The transport now treats an empty decoded SSE stream as a compatibility failure, logs raw/data/done SSE counters, and retries the same request as non-stream. This makes the provider's real error or final content visible instead of silently completing.
- Custom endpoint probing now treats `streaming_ok` as true only when a usable content/reasoning/tool delta appears, not merely when any SSE line or `[DONE]` appears.
- Local custom endpoints with unknown context metadata now use the profile fallback context window, currently 4096 tokens, instead of inheriting cloud heuristics such as 256K. If the endpoint/model reports a context window, or the user supplies manual `context_window` metadata, that provider/native value still wins and the normal cap policy applies.
- LM Studio/oMLX-style non-tool custom endpoints now use a compact plain-chat graph: Thoth disables tool injection for that run, uses a short assistant prompt, skips heavy skill/plugin/memory injection in `_pre_model_trim`, and keeps only lightweight date metadata plus trimmed conversation history. This preserves native custom-provider chat for small local contexts while keeping the full ReAct/tool graph for tool-capable providers.
- Live Windows smoke in an isolated Thoth data dir with LM Studio `qwen/qwen3.5-9b` and the normal enabled tool set now returns a visible answer through `stream_agent("hi", enabled_tools, ...)`.

## Follow-Up Fixes From Manual Validation

Manual validation surfaced three integration regressions to fix before shipping:

- Agent/tool-support errors must report the active thread model override, not the global default model. A local Ollama override such as `huihui_ai/deepseek-r1-abliterated:14b` should never be displayed as the default Codex model.
- The inline chat model picker must reliably clear a thread override when the user selects `Default`, even after switching from Default to another model within the same rendered picker.
- Catalog pin/default actions must not run synchronous picker refresh work on the Settings UI path. Post-pin refresh should be async-safe, provider/model option work should run through `run.io_bound`, and callback failures should be contained.

Implemented follow-up details:

- Agent provider/tool errors now resolve the active override from agent/model context vars before falling back to the global default, and provider refs are formatted for user-facing messages.
- The inline chat picker now tracks its current rendered value, can clear overrides back to `Default`, and checks local Ollama tool support before accepting a thread override.
- Catalog pin/default callbacks are async-aware and wrapped with failure handling. Settings picker refresh after catalog changes now collects model/media options through `run.io_bound`.
- Local Vision analysis now strips canonical provider refs before calling Ollama, so saved settings such as `model:ollama:gemma3:4b` preserve provider identity in Thoth but execute as `gemma3:4b` against Ollama.

Latest automated results:

- `pytest tests/test_vision_provider_refs.py tests/test_model_picker_regressions.py tests/test_provider_catalog.py tests/test_model_catalog_cache.py tests/test_provider_custom.py tests/test_setup_wizard_custom.py tests/test_context_policy.py tests/test_provider_selection.py tests/test_provider_resolution.py tests/test_provider_runtime.py tests/test_openai_compatible_transport.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` with `THOTH_DATA_DIR=D:\Code\Thoth\.tmp\thoth-test-data` -> 119 passed.
- `pytest tests/test_context_policy.py tests/test_context_override_runtime.py tests/test_openai_compatible_transport.py tests/test_provider_resolution.py tests/test_provider_runtime.py tests/test_model_picker_regressions.py tests/test_vision_provider_refs.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` with `THOTH_DATA_DIR=D:\Code\Thoth\.tmp\thoth-test-data` -> 57 passed.
- `pytest tests/test_provider_catalog.py tests/test_model_catalog_cache.py tests/test_provider_custom.py tests/test_setup_wizard_custom.py tests/test_context_policy.py tests/test_context_override_runtime.py tests/test_provider_selection.py tests/test_provider_resolution.py tests/test_provider_runtime.py tests/test_openai_compatible_transport.py tests/test_model_picker_regressions.py tests/test_vision_provider_refs.py tests/test_thoth_status_media.py tests/test_startup_hardening.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` with `THOTH_DATA_DIR=D:\Code\Thoth\.tmp\thoth-test-data` -> 144 passed.
- `pytest tests/test_provider_catalog.py tests/test_model_catalog_cache.py tests/test_provider_custom.py tests/test_setup_wizard_custom.py tests/test_context_policy.py tests/test_context_override_runtime.py tests/test_provider_selection.py tests/test_provider_resolution.py tests/test_provider_runtime.py tests/test_openai_compatible_transport.py tests/test_model_picker_regressions.py tests/test_vision_provider_refs.py tests/test_thoth_status_media.py tests/test_startup_hardening.py tests/test_chat_tool_trace_ui.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` with `THOTH_DATA_DIR=D:\Code\Thoth\.tmp\thoth-test-data` -> 148 passed.
- `pytest tests/test_openai_compatible_transport.py tests/test_model_picker_regressions.py tests/test_provider_custom.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` with `THOTH_DATA_DIR=D:\Code\Thoth\.tmp\thoth-test-data` -> 19 passed.
- `pytest tests/test_openai_compatible_transport.py tests/test_provider_custom.py tests/test_context_policy.py tests/test_model_picker_regressions.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` -> 33 passed.
- `pytest tests/test_provider_catalog.py tests/test_model_catalog_cache.py tests/test_provider_custom.py tests/test_setup_wizard_custom.py tests/test_context_policy.py tests/test_context_override_runtime.py tests/test_provider_selection.py tests/test_provider_resolution.py tests/test_provider_runtime.py tests/test_openai_compatible_transport.py tests/test_model_picker_regressions.py tests/test_vision_provider_refs.py tests/test_thoth_status_media.py tests/test_startup_hardening.py tests/test_chat_tool_trace_ui.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` with `THOTH_DATA_DIR=D:\Code\Thoth\.tmp\thoth-test-data` -> 162 passed.
- Isolated live LM Studio graph smoke with `THOTH_DATA_DIR=D:\Code\Thoth\.tmp\lmstudio-diag-tools3`, custom endpoint `http://127.0.0.1:1234/v1`, model `qwen/qwen3.5-9b`, and the normal enabled tool list -> reasoning tokens plus final visible text `Hi! How can I help you today?`.
- Direct LM Studio transport smoke via `ChatOpenAICompatible` against `http://127.0.0.1:1234/v1` and `qwen/qwen3.5-9b` -> visible content returned and `reasoning_content` preserved.
- Direct LM Studio fallback smoke with `last_probe.streaming_ok=false` -> 3 chunks: reasoning, final content, final marker.
- Direct LM Studio streaming smoke with `last_probe.streaming_ok=true` -> reasoning chunks plus final visible content.
- `pytest tests --ignore=tests/test_suite.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` with `THOTH_DATA_DIR=D:\Code\Thoth\.tmp\thoth-test-data` -> 502 passed, 5 skipped, 4 failed due missing optional dependencies (`telegram`, `langchain_community`).
- `pytest --collect-only tests --ignore=tests/test_suite.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` with `THOTH_DATA_DIR=D:\Code\Thoth\.tmp\thoth-test-data` -> 511 tests collected in 0.75s.
- `pytest --collect-only tests/test_suite.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` with `THOTH_DATA_DIR=D:\Code\Thoth\.tmp\thoth-test-data` -> no tests collected in 8.46s.
- `pytest tests/test_vision_provider_refs.py tests/test_thoth_status_media.py tests/test_startup_hardening.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` with `THOTH_DATA_DIR=D:\Code\Thoth\.tmp\thoth-test-data` -> 23 passed.
- `python -m compileall agent.py models.py providers ui vision.py tests -q` -> passed.
- `python -m compileall agent.py models.py providers ui vision.py tests -q` -> passed.
- `pytest tests/test_model_picker_regressions.py tests/test_provider_catalog.py tests/test_model_catalog_cache.py tests/test_provider_custom.py tests/test_setup_wizard_custom.py tests/test_context_policy.py tests/test_provider_selection.py tests/test_provider_resolution.py tests/test_provider_runtime.py tests/test_openai_compatible_transport.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` -> 116 passed.
- `python -m compileall agent.py ui providers models.py tests -q` -> passed.
- `pytest tests/test_provider_catalog.py tests/test_model_catalog_cache.py tests/test_context_policy.py tests/test_provider_selection.py tests/test_provider_resolution.py tests/test_provider_runtime.py tests/test_openai_compatible_transport.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` -> 100 passed.
- `pytest tests/test_provider_catalog.py tests/test_model_catalog_cache.py tests/test_provider_custom.py tests/test_setup_wizard_custom.py tests/test_context_policy.py tests/test_provider_selection.py tests/test_provider_resolution.py tests/test_provider_runtime.py tests/test_openai_compatible_transport.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` -> 112 passed.
- `python -m compileall providers ui models.py tests -q` -> passed.
- Full `pytest -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider` timed out after 300 seconds, and `pytest --collect-only -q ...` timed out after 120 seconds, so broad-suite collection needs a separate investigation.
