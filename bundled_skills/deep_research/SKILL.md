---
name: deep_research
display_name: Deep Research
icon: "🔬"
description: Perform multi-source research on a topic and produce a structured report.
enabled_by_default: false
version: "1.1"
tags:
  - research
  - analysis
activation:
  phrases:
    - deep research
    - research competitors
    - competitor research
    - structured report
    - multi-source research
    - look around
    - real story
    - investigate thoroughly
  keywords:
    - research
    - competitors
    - sources
    - report
    - market
    - compare
    - investigate
  negative_phrases:
    - meeting notes
    - action items
    - human tone
  examples:
    - I need to research competitors and produce a structured report
author: Thoth
---

When the user asks you to **research a topic in depth**, **write a research report**, or **investigate something thoroughly**, follow these steps:

1. **Check Existing Knowledge** — Before searching the web, review your recalled memories and use `search_memory` for the topic. The user may have researched this before, or you may already have saved facts, sources, or prior findings. Build on what you already know rather than starting from scratch.
2. **Clarify Scope** — If the topic is broad, ask one focused question to narrow it down before proceeding. Otherwise, proceed directly.
3. **Initial Search** — Run 2–3 web searches with varied queries to gather diverse perspectives on the topic.
4. **Source Deep-Dive** — Pick the 3–5 most promising URLs from the search results and read their full content using the URL reader.
5. **Academic Check** — If the topic is scientific or technical, search arXiv for relevant recent papers. Summarise key findings from the top 1–2 results.
6. **Synthesise** — Compile findings into a structured report:
   - **Executive Summary** — 2–3 sentence overview
   - **Key Findings** — Numbered list of the most important points
   - **Details** — Deeper discussion organised by sub-topic
   - **Open Questions** — What remains unclear or debated
   - **Sources** — List all URLs and papers referenced
7. **Cite Everything** — Every claim should reference its source with a numbered citation.
8. **Save Key Findings** — Save the most important facts and conclusions to memory so future research on this topic builds on today's work. Use specific entity categories (person, concept, project, fact) and link related findings together.

Aim for thoroughness over brevity. The user wants depth — give them a report they can act on.
