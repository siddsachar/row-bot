---
name: goal_guide
display_name: Goal Guide
icon: "flag"
description: Guidance for durable /goal progress tracking.
tools:
  - goal
tags: []
---
GOAL MODE:
- `/goal` owns the user-facing lifecycle. The user starts, pauses, resumes, clears, or completes goals with slash commands or the progress row.
- `goal_update` owns structured progress from you while a goal is active.
- Call `goal_update` when meaningful progress, evidence, blockers, next steps, or completion state changes.
- Do not claim goal completion without evidence. Include checks, files, child Agent results, citations, or user-visible artifacts when available.
- If blocked, describe the repeated blocker and the exact user decision or external change needed.
- If you need to keep working, update progress and next step, then continue normally. Goal Mode will decide whether to enqueue another internal continuation turn.
- Internal continuation prompts are audit/runtime context, not user messages. Do not quote or describe them as if the user sent them.
- The verifier uses the same model/provider as the active goal run with separate verification context. Do not mention or ask for a separate verifier model picker.
