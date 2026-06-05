---
name: brain_dump
display_name: Brain Dump
icon: "🧠"
description: Capture unstructured thoughts and organize them into structured notes saved to memory.
enabled_by_default: true
version: "1.2"
tags:
  - productivity
  - capture
activation:
  phrases:
    - brain dump
    - messy thoughts
    - dump thoughts
    - organize my thoughts
  keywords:
    - thoughts
    - messy
    - dump
    - organize
    - notes
    - capture
  negative_phrases:
    - meeting notes
    - structured report
  examples:
    - I just need to dump a bunch of messy thoughts
author: Row-Bot
---

When the user says they want to **brain dump**, **get thoughts out of their head**, or starts listing a stream of unstructured ideas/worries/plans, follow these steps:

1. **Listen First** — Let the user finish their dump. Don't interrupt or start processing until they signal they're done (or you detect a natural pause).
2. **Categorise** — Sort everything they mentioned into buckets:
   - 🎯 **Action Items** — Things that need doing
   - 💡 **Ideas** — Things to explore later
   - 🤔 **Decisions** — Things that need a decision
   - 📝 **Notes** — Things to just remember
3. **Prioritise Actions** — For the action items, suggest a priority order based on urgency and importance.
4. **Check Existing Knowledge** — Before saving, check recalled memories and use `search_memory` for key topics mentioned. If the user already brain-dumped about the same project or topic, update those existing memories instead of creating duplicates.
5. **Save to Memory** — Store action items, ideas, and notes to memory so nothing is lost. Use descriptive subjects like `Brain Dump — March 28 Actions` or topic-based subjects for easy retrieval. Link related items to existing entities (people, projects, events) in the knowledge graph.
6. **Summarise** — Present a clean overview of everything captured:
   - How many items in each category
   - What was saved to memory (new vs updated)
   - What still needs a decision

Keep the tone supportive and non-judgmental. The point is to get everything out of the user's head and into a trusted system.
