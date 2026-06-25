---
name: agents_guide
display_name: Agents Guide
icon: "hub"
description: Guidance for delegating focused work to child Agents.
tools:
  - agents
tags: []
---
AGENTS:
- Use Agents when the user explicitly asks you to delegate, parallelize, spawn helpers, review independently, research separately, test separately, or keep exploratory work out of the parent thread.
- Do not delegate ordinary short questions or simple single-step edits unless the user asks for agents or parallel work.
- Prefer focused profiles such as `research`, `plan`, `write`, `review`, or `develop` when they fit the task.
- Use advanced/internal profiles such as `worker`, `synthesize`, or `verify` only for scoped orchestration, synthesis, implementation, or verification work, and respect the active approval/workspace policy.

DELEGATING:
- Call `delegate_work` with a precise objective, a focused context packet, and a profile when useful.
- Give the child enough context to succeed without leaking the full parent transcript by default.
- Prefer async delegation: call `delegate_work(wait=false)` for ordinary child Agent work so the parent thread stays responsive and the user can keep chatting while the child runs.
- After `delegate_work(wait=false)`, tell the user the child Agent started. Use `agent_status` or `agent_wait` later only when the user asks what the child found or explicitly asks you to wait.
- Use `wait=true` only when the user explicitly asks you to wait for the child before answering, or when same-turn synthesis is truly required and cannot be deferred to a follow-up.
- For natural child-agent model requests like "use gpt5.5 via codex" or "use qwen 3.6 27 B via ollama", the parent agent must reason before delegation: inspect the complete pinned Brain choices with row_bot_status category='model', select the closest active pinned choice, and pass its canonical ref to `delegate_work(model=...)`. Leave `model` empty when the child should inherit the parent model. Do not pass raw natural phrases or unpinned provider refs.
- The `/agent` command is a direct command shortcut, not a natural-language planner. When a user explicitly types `/agent --model=model:provider:model-id ...`, the model value must be a strict active pinned Brain ref or exact pinned label.
- Child Agents cannot change their own runtime model with row_bot_update_setting. If the user wanted a child to use a different model, the parent must spawn it with `delegate_work(model=...)` or the user must use `/agent --model=...`.
- For artifact requests such as "use an agent to write/save/export a file", make the child Agent create the artifact when its profile has the needed write tool. Do not ask the child to return raw content for the parent to export unless the user explicitly wants parent-side synthesis or packaging.
- Do not give child Agents recursive delegation access unless the selected profile explicitly allows it.

TRACKING:
- Use `agent_status` to inspect running, waiting, stopped, failed, or completed child Agents.
- Use `agent_wait` when the user explicitly asks for the child result or when a later parent turn genuinely needs the child result before answering.
- Use `agent_message` to record a parent steering/follow-up message for a non-terminal child Agent. Messages sent while the child is queued are included before it starts; active model calls cannot be interrupted mid-call.
- Use `agent_stop` when a child is obsolete, stuck, or the user asks to stop it.
- Summarize child results for the user; do not dump long raw logs unless asked.

PROFILES:
- Use `agent_profiles` to discover available built-in and user Agent Profiles.
- Agent Profiles may narrow inherited tools and pin profile-specific skills. Selected `allow_tools` are the hard runtime tool boundary; profiles with no allow-list inherit all globally enabled tools. Respect `allow_tools`, `skills`, context mode, workspace mode, and approval caps.
- Generic direct Agent requests use the `worker` profile. Select a specialized profile only when the user explicitly names it, such as "use a review agent to..." or `/agent review ...`, or when you are calling `delegate_work` and can justify the profile in your visible handoff. Old folded names such as `quality_reviewer` are accepted as aliases, but canonical slugs are preferred.
- Use `agent_profile_save` only after the user explicitly asks to create or update a reusable Agent Profile. This action is approval-gated.
- Use `agent_promote` only when the user explicitly wants to turn a completed run into a reusable profile or workflow. Workflow promotion creates a disabled manual workflow for review before enabling or scheduling.

HANDOFF:
- When synthesizing child results, state which Agents ran, their status, key evidence, conflicts or uncertainty, and the next action.
