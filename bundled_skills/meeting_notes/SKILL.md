---
name: meeting_notes
display_name: Meeting Notes
icon: "📋"
description: Structure raw meeting notes into actionable minutes with follow-ups.
enabled_by_default: true
version: "1.2"
tags:
  - productivity
  - meetings
activation:
  phrases:
    - meeting notes
    - action items
    - meeting transcript
    - minutes
  keywords:
    - meeting
    - notes
    - transcript
    - decisions
    - follow-ups
    - actions
  negative_phrases:
    - competitor research
    - structured report
  examples:
    - Summarize these meeting notes and extract action items
author: Row-Bot
---

When the user shares **meeting notes**, asks you to **summarise a meeting**, or says they just finished a meeting, follow these steps:

1. **Parse the Input** — Read through the raw notes, transcript, or description the user provides.
2. **Identify Participants** — List everyone mentioned or involved.
3. **Structure the Minutes** — Organise into:
   - **Meeting Title & Date**
   - **Attendees**
   - **Key Discussion Points** — Summarise each topic discussed in 1–2 sentences
   - **Decisions Made** — Bullet list of any decisions reached
   - **Action Items** — For each action item, capture:
     - What needs to be done
     - Who is responsible
     - When it's due (if mentioned)
4. **Save to Knowledge Graph** — Store action items, decisions, and key facts to memory. Save participants as `person` entities if they don't already exist — they'll auto-link in the knowledge graph and appear in the wiki, making them searchable across all future meetings. Use descriptive subjects like `Team Standup Actions — March 28`. Link action items to the people responsible.
5. **Schedule Follow-ups** — If any follow-up meetings were mentioned, offer to create calendar events.
6. **Present** — Output the structured minutes in a clean, skimmable format.

Keep the language professional but concise. The goal is to turn messy notes into something the user can share with their team.
