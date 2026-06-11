---
name: self_reflection
display_name: Self-Reflection
icon: mirror
description: Periodically review memory for contradictions, gaps, stale information, and controlled improvement proposals.
enabled_by_default: true
version: "1.2"
tags:
  - memory
  - quality
activation:
  phrases:
    - stale memory
    - clean stale memory
    - review memory
    - what you know about me
    - contradictions in memory
  keywords:
    - stale
    - memory
    - contradictions
    - gaps
    - review
    - cleanup
  negative_phrases:
    - knowledge base document
    - meeting notes
  examples:
    - Review what you know about me and clean stale memory
author: Row-Bot
---

When the user asks you to review your memories, check what you know, clean up your knowledge, or when you notice a potential contradiction in recalled memories, apply this process:

## Contradiction Detection

1. **Flag Conflicts** - When recalled memories contradict each other, surface the conflict to the user immediately. Do not silently pick one.
2. **Ask, Don't Assume** - Say exactly what conflicts you see and ask the user which version is correct. Then update the wrong memory and confirm the fix.
3. **Check Dates** - When you see a memory that might be outdated, mention it and ask whether it is still current.

## Memory Audit

4. **Get the Baseline** - Start with `wiki_stats` to see total articles, conversations, and vault health. Then use `search_memory` with broad terms to scan for coverage gaps.
5. **Systematic Sweep** - Use `search_memory` with broad category queries such as person, preference, fact, event, project, and place. Use `explore_connections` to visualize relationships and spot gaps.
6. **Review Quality** - Look for duplicates, stale entries, user-only connections, and missing links.
7. **Fix With Consent** - Update or `link_memories` during the audit when the user has confirmed the correction. Confirm each change.
8. **Rebuild After Cleanup** - After bulk updates, run `wiki_rebuild` to regenerate the wiki vault.
9. **Summarize** - After the audit, give a brief count of memories reviewed, updated, and linked, and flag anything that needs the user's input.

## Ongoing Awareness

10. **Correction Logging** - When the user corrects you on a fact, update the existing memory and briefly acknowledge the correction.
11. **Confidence Signals** - If you recall a memory but are not confident it is still accurate, say so and ask.

## Insights And Evolution

12. **Check Automated Insights** - During reflection, use `row_bot_status` with category `insights` to see active insights and linked proposals. Use category `evolution` to inspect proposals, action runs, rejection memory, and curator dry-run summaries.
13. **Present Controlled Actions** - For each active insight, summarize the category, severity, suggestion, linked proposal type, risk, confidence, and action status. Group related items by category.
14. **Use Proposals, Not Direct Edits** - Do not edit `insights.json`, memory files, skills, tool guides, settings, or code directly during reflection. For skill improvements, use `row_bot_create_skill` or `row_bot_patch_skill` to create proposals only, then ask the user to preview and approve with `row_bot_apply_proposal`.
15. **Send Feedback Separately** - For app bugs, tool/config problems, or system-health issues, create a redacted `row_bot_send_feedback` proposal instead of turning the issue into a skill. Do not include full logs or diagnostic bundles unless the user explicitly approves; the user can copy the report or submit it through the Row-Bot contact page.
16. **Learn From Outcomes** - If the user rejects a proposal, record the reason with `row_bot_reject_proposal`. Mark proposals verified only after explicit validation or user confirmation with `row_bot_verify_proposal`.
