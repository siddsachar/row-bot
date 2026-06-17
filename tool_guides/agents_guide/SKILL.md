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
- Prefer read-only profiles such as `explorer`, `researcher`, `docs_researcher`, and `reviewer` for parallel work.
- Use write-capable profiles such as `worker` or `tester` only for scoped implementation or verification work, and respect the active approval/workspace policy.

DELEGATING:
- Call `delegate_work` with a precise objective, a focused context packet, and a profile when useful.
- Give the child enough context to succeed without leaking the full parent transcript by default.
- `delegate_work` starts background work by default. Use `wait=true` only when the parent cannot continue until the child result is available.
- For artifact requests such as "use an agent to write/save/export a file", make the child Agent create the artifact when its profile has the needed write tool. Do not ask the child to return raw content for the parent to export unless the user explicitly wants parent-side synthesis or packaging.
- Do not give child Agents recursive delegation access unless the selected profile explicitly allows it.

TRACKING:
- Use `agent_status` to inspect running, waiting, stopped, failed, or completed child Agents.
- Use `agent_wait` when you need a child result before answering the user.
- Use `agent_message` to record a parent steering/follow-up message for a non-terminal child Agent. Messages sent while the child is queued are included before it starts; active model calls cannot be interrupted mid-call.
- Use `agent_stop` when a child is obsolete, stuck, or the user asks to stop it.
- Summarize child results for the user; do not dump long raw logs unless asked.

PROFILES:
- Use `agent_profiles` to discover available built-in and user Agent Profiles.
- Agent Profiles may narrow inherited tools and pin profile-specific skills. Respect `allow_tools`, `skills`, context mode, workspace mode, and approval caps.
- Generic direct Agent requests use the `worker` profile. Select a specialized profile only when the user explicitly names it, such as "use a reviewer agent to..." or `/agent reviewer ...`, or when you are calling `delegate_work` and can justify the profile in your visible handoff.
- Use `agent_profile_save` only after the user explicitly asks to create or update a reusable Agent Profile. This action is approval-gated.
- Use `agent_promote` only when the user explicitly wants to turn a completed run into a reusable profile or workflow. Workflow promotion creates a disabled manual workflow for review before enabling or scheduling.

HANDOFF:
- When synthesizing child results, state which Agents ran, their status, key evidence, conflicts or uncertainty, and the next action.
