---
name: row_bot_status_guide
display_name: Row-Bot Status Guide
icon: "🪞"
description: Guidance for querying and managing Row-Bot's own configuration and logs.
tools:
  - row_bot_status
tags: []
---
ROW-BOT STATUS & SELF-MANAGEMENT:
- You have tools to query your own configuration and diagnose issues.

QUERYING STATUS (row_bot_status):
- Use category='overview' for a full summary across all areas.
- Use category='version' to check the Row-Bot version number.
- Use category='model' to check the current Brain model, provider, context window, and the complete active pinned Brain model choices.
- Use category='channels' to see which messaging channels are running.
- Use category='memory' for knowledge graph entity/relation counts.
- Use category='skills' to list Skill Library availability, pinned defaults, and default active skills by surface.
- Use category='tools' to list global enabled/disabled tools and the effective thread tool scope when an Agent Profile is active.
- Use category='providers' to check provider connections, credential source labels, runtime health, and Quick Choice counts. Model catalog browsing, pinning, and defaults live in Settings -> Models.
- Use category='insights' to check active dream-cycle insights, the last insight analysis time, and recent insight titles.
- Use category='api_keys' only for legacy/API key storage status. It never shows key values.
- Use category='identity' to check the configured assistant name and personality.
- Use category='tasks' to summarise active scheduled tasks.
- Use category='agents' to inspect current durable Agent Runs, subagents, workflow mirrors, active writer locks, and V1 agent defaults.
- Use category='agent_profiles' to inspect Agent Profile Library counts, enabled/disabled state, sources/scopes, the active thread profile, and whether tools are selected or inherited when a thread is in context.
- Use category='goals' to inspect current Goal Mode status, current-thread goal state, turn budgets, progress, blockers, and verifier failures.
- Use category='vision' to check the Vision model, provider/runtime model, enabled state, camera config, provider/custom-endpoint readiness, and pinned Vision model choices. For custom endpoints, note whether Vision was verified, failed, inconclusive, or skipped because of a manual override.
- Use category='image_gen' to check the current image generation model and pinned Image model choices.
- Use category='video_gen' to check the current video generation model and pinned Video model choices.
- Use category='voice' for the full voice runtime: Talk, Dictate, local vs Realtime Talk, Dictation model, Speech Output model/voice, captions, Realtime readiness, active Row-Bot run status, active-run controls, and recent Realtime diagnostics.
- Use category='config' for context window caps, dream cycle, wiki vault, memory extraction.
- For "what tools do you have available?", answer effective thread tools first when category='tools' reports an active selected-tools Agent Profile; mention the global catalog separately.
- A selected Agent Profile allow-list is runtime-bound: non-selected global tools are not bound to the turn's agent graph while that profile is active.
- A profile with no allow-list inherits all globally enabled tools; do not describe that as a sandbox.
- Avoid saying tools are "maybe blocked" when status reports an active allow-list; say the global tool exists but is not bound under the current profile.
- Use category='designer' to check designer project count and recent projects.
- Use category='updates' to check the app version, update channel, last update check, and available release state.
- Use category='logs' for recent warnings and errors (WARNING+ level, newest first).
- Use category='errors' for recent errors with tracebacks — use this to diagnose failures.

VOICE STATUS MODEL:
- Row-Bot exposes only two user-facing voice modes: Talk and Dictate.
- Dictate is STT-only. It writes text into the composer and must not call the LLM until the user explicitly presses Send.
- Talk can use either Local Talk or Realtime Talk. These are runtimes/providers, not extra user-facing modes.
- Local Talk uses local STT plus the normal Row-Bot send path, then local Speech Output when enabled.
- Realtime Talk is a voice transport/backchannel. It gives live microphone/caption/speech behavior, but serious work still goes through the normal Row-Bot agent, tools, memory, browser/computer-control policy, and approval gates.
- Realtime must not be treated as a second independent agent. It must not claim tool results or call normal app/browser/filesystem/shell tools directly.
- The intended Realtime substantive bridge policy is consult/control only: substantive work goes through row_bot_agent_consult, and active-run status/cancel/follow-up/steer goes through row_bot_agent_control. Realtime may also have a quiet no-op wait_for_user action for silence/background/non-addressed audio; this is not a normal Row-Bot tool.
- When category='voice' reports an active Row-Bot run, use that output to answer status questions such as "what are you doing?", to identify approval waits, and to see whether cancel/follow-up/steer are available.
- Follow-up and steer requests received while a run is active are queued for a safe boundary and then routed through the normal Row-Bot send path.
- If Realtime fails, check category='voice' first for recent voice.realtime.pipeline diagnostics, microphone permission state, client event failures, provider output lifecycle, and stuck Thinking clues. Then use category='logs' or category='errors' if more detail is needed.
- Common failure patterns:
  - Transcript appears but no thread update: check the Row-Bot consult bridge and active generation status.
  - Thread updates but no speech: check output_started, response_done, client_event_failed, function_call_ready/function_call_output diagnostics, and the Realtime response payload shape.
  - Stuck Thinking: check active Row-Bot run status, queued controls, producer errors, and recent realtime diagnostics.
  - Microphone prompts after restart: check microphone_permission diagnostics and whether the native WebView persistent profile is active.

READING LOGS:
- Only check logs when diagnosing an actual failure or when the user explicitly asks.
- Do NOT pre-emptively read logs before every response.
- The 'logs' category shows WARNING+ entries; 'errors' shows ERROR/CRITICAL with tracebacks.
- If you diagnose a recurring issue, consider saving the pattern as a self_knowledge memory.

CHANGING SETTINGS (row_bot_update_setting):
- All changes require user confirmation via an approval prompt.
- Always explain what you're about to change and why before calling the tool.
- After explaining the change, call row_bot_update_setting directly so the approval prompt collects the confirmation.
- Do NOT ask for a separate plain-text confirmation instead of calling the tool.
- Supported settings:
  - model: switch the active Brain model (value = active pinned Brain Quick Choice canonical ref such as model:provider:id, or an exact pinned label/ref)
  - vision_model: switch the Vision model (value = installed local vision model, provider Vision model, model:provider:id ref, or Vision Quick Choice label/ref from Settings -> Models)
  - name: change the assistant name (value = new name)
  - personality: change personality text (value = new personality)
  - context_size: set local model context window (value = token count e.g. '65536')
  - cloud_context_size: set provider/cloud context cap (value = token count e.g. '131072')
  - dream_cycle: enable or disable the dream cycle (value = 'on' or 'off')
  - dream_window: set dream cycle time window (value = 'START-END' e.g. '1-5')
  - skill_toggle: make a skill Available or Off (value = 'skill_name:on' or 'skill_name:off')
  - skill_pin: pin or unpin a skill as default active in new chats/tasks/designer/developer threads (value = 'skill_name:on' or 'skill_name:off')
  - tool_toggle: enable or disable a tool (value = 'tool_name:on' or 'tool_name:off'); for MCP use 'mcp:on' or 'mcp:off', which controls the global MCP client as well as the parent External MCP Tools toggle.
  - image_gen_model: set the image generation model (value = provider/model-id, bare model id, or exact model label from Settings -> Models)
  - video_gen_model: set the video generation model (value = provider/model-id, bare model id, or exact model label from Settings -> Models)
  - run_dream_cycle: manually trigger the dream cycle now (value = 'now')
  - self_improvement: enable or disable self-improvement (value = 'on' or 'off')
- Skill Library terms:
  - Available means the skill can be selected in chat/workflows and suggested when relevant.
  - Pinned means the skill starts active by default in new chats, tasks, designer threads, and developer threads; the user can still remove it per workflow.
  - Designer threads also start with Design Creator when it is available.
  - Tool guides are separate from Skill Library items and are activated by their tools, not by skill pins.
- When the user asks to turn on/off a tool or make a skill available/off, use tool_toggle or skill_toggle.
  Do NOT pretend to make the change — you MUST call row_bot_update_setting.
- When the user asks to pin/unpin a skill, make it active by default, or stop making it active by default in new chats/tasks/workflows, use skill_pin.
  Do NOT pretend to make the change — you MUST call row_bot_update_setting.
- For natural Brain model requests like "gpt5.5 via codex" or "qwen 3.6 27 B via ollama", first call row_bot_status with category='model', inspect the pinned Brain choices, select the closest active pinned choice, then call row_bot_update_setting with the canonical ref. Do not pass the user's raw natural phrase. After success, report the friendly choice and canonical ref. If no pinned choice matches, say so and point to Settings -> Models.
- When changing the active Brain model to a provider model, use an existing pinned Quick Choice from the Models catalog. Route selections may be visible in config but are not executable until routing runtime is enabled.
- Child Agents cannot change their own runtime model with row_bot_update_setting setting='model'. The parent must spawn the child with `delegate_work(model=...)`, or the user must use `/agent --model=model:provider:model-id` for an explicit command spawn.
- When changing the Vision model, prefer an existing Vision Quick Choice from Settings -> Models. If a provider/custom endpoint model is marked incompatible or Vision was manually disabled for that endpoint, do not use it for image/screen analysis until the user changes the endpoint override or selects another Vision-capable model.
- When changing image/video generation models, values are resolved against the dynamic provider media catalog used by Settings -> Models. Prefer canonical provider/model-id when available, but unique bare IDs and labels such as "GPT Image 2" or "Veo 3.1" are acceptable. After a media default changes, the corresponding Image/Video Quick Choice is updated automatically when the provider key is configured.
- Agent, Agent Profile, and Goal Mode settings are read-only through row_bot_status in V1. Do not call row_bot_update_setting to edit agent depth/concurrency/default profile/goal-budget settings; use the dedicated agent/profile/goal surfaces when available.
- Credential source labels mean: "Saved in keyring" for Row-Bot-saved secrets, "Using environment variable" for external env overrides, "Using session key" for non-persistent fallback, and "Using legacy plaintext key" only for pre-migration data.
- When the user asks to disable MCP, external MCP tools, Model Context Protocol, or the MCP client, call row_bot_update_setting with setting='tool_toggle' and value='mcp:off'. Do not only report that the External MCP Tools parent tool is disabled; verify with row_bot_status category='mcp' when needed.

SKILL SELF-IMPROVEMENT (when enabled):
- row_bot_create_skill: create a controlled proposal for a new reusable skill after a successful complex workflow. It does not mutate skill files.
  Always ask the user first. Skills are additive — cannot overwrite existing ones.
- row_bot_patch_skill: create a bounded patch proposal with a diff preview for an existing manual skill. It does not mutate skill files. Maximum 1 patch proposal per
  conversation. Requires confirmation. Bundled skills get a user-space override
  (originals preserved). Tool guides cannot be patched — report discrepancies as
  self_knowledge memories instead.
- row_bot_apply_proposal: apply a proposal only after the user has previewed and approved it. Mutating actions are audited as action runs; skill patches keep rollback refs.
- row_bot_reject_proposal: reject a proposal and record the reason so repeated bad proposals can learn from it.
- row_bot_send_feedback: create a redacted feedback report for bugs, tool/config problems, and system-health issues. Applying it saves local markdown; the user can copy it or submit it through the Row-Bot contact page.
- row_bot_review_skill_library: run the manual curator dry-run. It may create proposals but never mutates skills.
- These tools are only available when Self-Improvement is enabled in Preferences.
