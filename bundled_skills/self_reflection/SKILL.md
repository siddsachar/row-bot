---
name: self_reflection
display_name: Self-Reflection
icon: "🪞"
description: Periodically review memory for contradictions, gaps, and stale information.
enabled_by_default: false
version: "1.1"
tags:
  - memory
  - quality
author: Thoth
---

When the user asks you to **review your memories**, **check what you know**, **clean up your knowledge**, or when you notice a potential contradiction in recalled memories, apply this process:

## Contradiction Detection

1. **Flag Conflicts** — When recalled memories contradict each other (e.g. two different birthdays for the same person, or "lives in London" vs. "moved to Berlin"), surface the conflict to the user immediately. Don't silently pick one.
2. **Ask, Don't Assume** — Say exactly what conflicts you see and ask the user which version is correct. Then update the wrong memory and confirm the fix.
3. **Check Dates** — When you see a memory that might be outdated (job titles, addresses, project statuses), mention it: *"I have that you work at X — is that still current?"*

## Memory Audit (when explicitly requested)

4. **Get the Baseline** — Start with `wiki_stats` to see total articles, conversations, and vault health. Then use `search_memory` with broad terms to scan for coverage gaps — it now includes semantic, keyword, and graph-expansion search in one call.
5. **Systematic Sweep** — Use `search_memory` with broad category queries (person, preference, fact, event, project, place) to surface everything. Use `explore_connections` to visualise relationships and spot gaps. Review each category for:
   - **Duplicates** — Same fact stored under different wording
   - **Stale entries** — Jobs, addresses, or statuses that may have changed
   - **User-only connections** — Entities whose only relationship is the auto-link to User (visible in the graph panel with "Hide unlinked" toggle) — these need richer connections
   - **Missing links** — Related memories that aren't connected (e.g. a person and their workplace)
6. **Fix As You Go** — Update or `link_memories` during the audit rather than compiling a report first. Confirm each change with the user.
7. **Rebuild after cleanup** — After bulk updates (merging duplicates, fixing links), run `wiki_rebuild` to regenerate the wiki vault with clean, up-to-date articles.
8. **Summary** — After the audit, give a brief count: how many memories reviewed, how many updated, how many linked, and flag any that need the user's input.

## Ongoing Awareness

9. **Correction Logging** — When the user corrects you on a fact ("Actually, it's March 20, not March 15"), always update the existing memory. After updating, briefly acknowledge the correction so the user knows it stuck.
10. **Confidence Signals** — If you recall a memory but aren't confident it's still accurate (e.g. it's about a fast-changing topic like a project status), say so: *"Last I saved, the deadline was June 1 — is that still the plan?"*

## Insights Review

11. **Check Automated Insights** — When performing a reflection, also check for pending insights from the dream cycle. Use `thoth_status` with category `insights` to see active insight counts, last analysis time, and recent insight titles; read `~/.thoth/insights.json` only when you need the full bodies/evidence or need to update statuses.
12. **Present Insights** — For each active insight (status "new" or "pinned"), summarize it for the user with category, severity, and the suggestion. Group by category.
13. **Act on Insights** — Ask the user what to do with each insight: dismiss it, investigate further, or apply the suggestion. For skill proposals with drafts, offer to create the skill. After the user decides, update the insight status accordingly by editing the insights file.
