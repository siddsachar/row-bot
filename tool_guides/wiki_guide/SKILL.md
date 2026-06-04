---
name: wiki_guide
display_name: Wiki Guide
icon: "📝"
description: Guidance for the Obsidian-compatible wiki vault.
tools:
  - wiki
tags: []
---
WIKI VAULT:
- The wiki vault is an Obsidian-compatible, auto-generated view of your
  knowledge graph.  Every entity saved via save_memory and every relation
  created via link_memories is automatically exported as a markdown file
  with [[wiki-links]] and YAML frontmatter.
- The wiki is READ-ONLY from the agent's perspective.  NEVER write or
  modify wiki files directly via filesystem tools — that would cause
  out-of-sync files.  Instead:
  • To add knowledge → use save_memory / link_memories (wiki updates
    automatically via the knowledge graph pipeline).
  • To export the current conversation as a wiki page → use
    wiki_export_conversation.
  • To read a wiki article → use wiki_read.
  • To get vault statistics → use wiki_stats.
  • To force a full rebuild → use wiki_rebuild.
- When the user says "save to my wiki", "add this to my notes", or
  similar: save the information via save_memory (the wiki page will be
  created/updated automatically).  Do NOT use workspace_write_file.
- Users can edit wiki files externally (e.g. in Obsidian).  Row-Bot
  detects file modifications via mtime comparison and syncs changes
  back into the knowledge graph automatically.
