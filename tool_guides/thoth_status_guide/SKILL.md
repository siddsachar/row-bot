---
name: thoth_status_guide
display_name: Thoth Status Guide
icon: "🪞"
description: Guidance for querying and managing Thoth's own configuration and logs.
tools:
  - thoth_status
tags: []
---
THOTH STATUS & SELF-MANAGEMENT:
- You have tools to query your own configuration and diagnose issues.

QUERYING STATUS (thoth_status):
- Use category='overview' for a full summary across all areas.
- Use category='version' to check the Thoth version number.
- Use category='model' to check the current model, provider, and context window.
- Use category='channels' to see which messaging channels are running.
- Use category='memory' for knowledge graph entity/relation counts.
- Use category='skills' to list enabled and disabled skills.
- Use category='tools' to list enabled and disabled tools.
- Use category='providers' to check provider connections, credential source labels, runtime health, and Quick Choice counts. Model catalog browsing, pinning, and defaults live in Settings -> Models.
- Use category='insights' to check active dream-cycle insights, the last insight analysis time, and recent insight titles.
- Use category='api_keys' only for legacy/API key storage status. It never shows key values.
- Use category='identity' to check the configured assistant name and personality.
- Use category='tasks' to summarise active scheduled tasks.
- Use category='vision' to check the Vision model, provider/runtime model, enabled state, camera config, and provider/custom-endpoint readiness. For custom endpoints, note whether Vision was verified, failed, inconclusive, or skipped because of a manual override.
- Use category='image_gen' to check the current image generation model.
- Use category='video_gen' to check the current video generation model.
- Use category='voice' for the full voice runtime: Talk, Dictate, local vs Realtime Talk, Dictation model, Speech Output model/voice, captions, Realtime readiness, active Thoth run status, active-run controls, and recent Realtime diagnostics.
- Use category='config' for context window caps, dream cycle, wiki vault, memory extraction.
- Use category='designer' to check designer project count and recent projects.
- Use category='updates' to check the app version, update channel, last update check, and available release state.
- Use category='logs' for recent warnings and errors (WARNING+ level, newest first).
- Use category='errors' for recent errors with tracebacks — use this to diagnose failures.

VOICE STATUS MODEL:
- Thoth exposes only two user-facing voice modes: Talk and Dictate.
- Dictate is STT-only. It writes text into the composer and must not call the LLM until the user explicitly presses Send.
- Talk can use either Local Talk or Realtime Talk. These are runtimes/providers, not extra user-facing modes.
- Local Talk uses local STT plus the normal Thoth send path, then local Speech Output when enabled.
- Realtime Talk is a voice transport/backchannel. It gives live microphone/caption/speech behavior, but serious work still goes through the normal Thoth agent, tools, memory, browser/computer-control policy, and approval gates.
- Realtime must not be treated as a second independent agent. It must not claim tool results or call normal app/browser/filesystem/shell tools directly.
- The intended Realtime substantive bridge policy is consult/control only: substantive work goes through thoth_agent_consult, and active-run status/cancel/follow-up/steer goes through thoth_agent_control. Realtime may also have a quiet no-op wait_for_user action for silence/background/non-addressed audio; this is not a normal Thoth tool.
- When category='voice' reports an active Thoth run, use that output to answer status questions such as "what are you doing?", to identify approval waits, and to see whether cancel/follow-up/steer are available.
- Follow-up and steer requests received while a run is active are queued for a safe boundary and then routed through the normal Thoth send path.
- If Realtime fails, check category='voice' first for recent voice.realtime.pipeline diagnostics, microphone permission state, client event failures, provider output lifecycle, and stuck Thinking clues. Then use category='logs' or category='errors' if more detail is needed.
- Common failure patterns:
  - Transcript appears but no thread update: check the Thoth consult bridge and active generation status.
  - Thread updates but no speech: check output_started, response_done, client_event_failed, function_call_ready/function_call_output diagnostics, and the Realtime response payload shape.
  - Stuck Thinking: check active Thoth run status, queued controls, producer errors, and recent realtime diagnostics.
  - Microphone prompts after restart: check microphone_permission diagnostics and whether the native WebView persistent profile is active.

READING LOGS:
- Only check logs when diagnosing an actual failure or when the user explicitly asks.
- Do NOT pre-emptively read logs before every response.
- The 'logs' category shows WARNING+ entries; 'errors' shows ERROR/CRITICAL with tracebacks.
- If you diagnose a recurring issue, consider saving the pattern as a self_knowledge memory.

CHANGING SETTINGS (thoth_update_setting):
- All changes require user confirmation via an approval prompt.
- Always explain what you're about to change and why before calling the tool.
- After explaining the change, call thoth_update_setting directly so the approval prompt collects the confirmation.
- Do NOT ask for a separate plain-text confirmation instead of calling the tool.
- Supported settings:
  - model: switch the active LLM (value = local model name, provider model id, model:provider:id ref, or Quick Choice label/ref)
  - vision_model: switch the Vision model (value = installed local vision model, provider Vision model, model:provider:id ref, or Vision Quick Choice label/ref from Settings -> Models)
  - name: change the assistant name (value = new name)
  - personality: change personality text (value = new personality)
  - context_size: set local model context window (value = token count e.g. '65536')
  - cloud_context_size: set provider/cloud context cap (value = token count e.g. '131072')
  - dream_cycle: enable or disable the dream cycle (value = 'on' or 'off')
  - dream_window: set dream cycle time window (value = 'START-END' e.g. '1-5')
  - skill_toggle: enable or disable a skill (value = 'skill_name:on' or 'skill_name:off')
  - tool_toggle: enable or disable a tool (value = 'tool_name:on' or 'tool_name:off'); for MCP use 'mcp:on' or 'mcp:off', which controls the global MCP client as well as the parent External MCP Tools toggle.
  - image_gen_model: set the image generation model (value = provider/model-id, bare model id, or exact model label from Settings -> Models)
  - video_gen_model: set the video generation model (value = provider/model-id, bare model id, or exact model label from Settings -> Models)
  - run_dream_cycle: manually trigger the dream cycle now (value = 'now')
  - self_improvement: enable or disable self-improvement (value = 'on' or 'off')
- When the user asks to turn on/off a tool or skill, use tool_toggle or skill_toggle.
  Do NOT pretend to make the change — you MUST call thoth_update_setting.
- When changing the active model to a provider model, prefer an existing Quick Choice from the Models catalog. Route selections may be visible in config but are not executable until routing runtime is enabled.
- When changing the Vision model, prefer an existing Vision Quick Choice from Settings -> Models. If a provider/custom endpoint model is marked incompatible or Vision was manually disabled for that endpoint, do not use it for image/screen analysis until the user changes the endpoint override or selects another Vision-capable model.
- When changing image/video generation models, values are resolved against the dynamic provider media catalog used by Settings -> Models. Prefer canonical provider/model-id when available, but unique bare IDs and labels such as "GPT Image 2" or "Veo 3.1" are acceptable. After a media default changes, the corresponding Image/Video Quick Choice is updated automatically when the provider key is configured.
- Credential source labels mean: "Saved in keyring" for Thoth-saved secrets, "Using environment variable" for external env overrides, "Using session key" for non-persistent fallback, and "Using legacy plaintext key" only for pre-migration data.
- When the user asks to disable MCP, external MCP tools, Model Context Protocol, or the MCP client, call thoth_update_setting with setting='tool_toggle' and value='mcp:off'. Do not only report that the External MCP Tools parent tool is disabled; verify with thoth_status category='mcp' when needed.

SKILL SELF-IMPROVEMENT (when enabled):
- thoth_create_skill: create a new reusable skill after a successful complex workflow.
  Always ask the user first. Skills are additive — cannot overwrite existing ones.
- thoth_patch_skill: improve an existing skill's instructions. Maximum 1 patch per
  conversation. Requires confirmation. Bundled skills get a user-space override
  (originals preserved). Tool guides cannot be patched — report discrepancies as
  self_knowledge memories instead.
- These tools are only available when Self-Improvement is enabled in Preferences.
