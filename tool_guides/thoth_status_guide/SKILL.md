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
- Use category='api_keys' to check which providers are configured (never shows key values).
- Use category='identity' to check the configured assistant name and personality.
- Use category='tasks' to summarise active scheduled tasks.
- Use category='vision' to check vision model, enabled state, and camera config.
- Use category='image_gen' to check the current image generation model.
- Use category='video_gen' to check the current video generation model.
- Use category='voice' for TTS voice, speed, enabled state, and Whisper STT model.
- Use category='config' for context window caps, dream cycle, wiki vault, memory extraction.
- Use category='designer' to check designer project count and recent projects.
- Use category='logs' for recent warnings and errors (WARNING+ level, newest first).
- Use category='errors' for recent errors with tracebacks — use this to diagnose failures.

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
  - model: switch the active LLM (value = model name)
  - name: change the assistant name (value = new name)
  - personality: change personality text (value = new personality)
  - context_size: set local model context window (value = token count e.g. '65536')
  - cloud_context_size: set cloud context cap (value = token count e.g. '131072')
  - dream_cycle: enable or disable the dream cycle (value = 'on' or 'off')
  - dream_window: set dream cycle time window (value = 'START-END' e.g. '1-5')
  - skill_toggle: enable or disable a skill (value = 'skill_name:on' or 'skill_name:off')
  - tool_toggle: enable or disable a tool (value = 'tool_name:on' or 'tool_name:off'); for MCP use 'mcp:on' or 'mcp:off', which controls the global MCP client as well as the parent External MCP Tools toggle.
  - image_gen_model: set the image generation model (value = provider/model-id)
  - video_gen_model: set the video generation model (value = provider/model-id)
  - run_dream_cycle: manually trigger the dream cycle now (value = 'now')
  - self_improvement: enable or disable self-improvement (value = 'on' or 'off')
- When the user asks to turn on/off a tool or skill, use tool_toggle or skill_toggle.
  Do NOT pretend to make the change — you MUST call thoth_update_setting.
- When the user asks to disable MCP, external MCP tools, Model Context Protocol, or the MCP client, call thoth_update_setting with setting='tool_toggle' and value='mcp:off'. Do not only report that the External MCP Tools parent tool is disabled; verify with thoth_status category='mcp' when needed.

SKILL SELF-IMPROVEMENT (when enabled):
- thoth_create_skill: create a new reusable skill after a successful complex workflow.
  Always ask the user first. Skills are additive — cannot overwrite existing ones.
- thoth_patch_skill: improve an existing skill's instructions. Maximum 1 patch per
  conversation. Requires confirmation. Bundled skills get a user-space override
  (originals preserved). Tool guides cannot be patched — report discrepancies as
  self_knowledge memories instead.
- These tools are only available when Self-Improvement is enabled in Preferences.
