---
name: shell_guide
display_name: Shell Guide
icon: "💻"
description: Guidance for shell commands and system info queries.
tools:
  - shell
tags: []
---
  * For system resource queries (CPU, RAM, disk, battery, IP, uptime),
    prefer get_system_info — one call, structured output, no shell needed.
  * A read-only command, or one piped into sort, pick or count filters, runs without asking, even in a
    Block workflow: `Get-ChildItem -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object
    -First 20 FullName,LastWriteTime`, `ls -lt | head -20`. Where-Object may compare properties with plain
    values: `Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-1) -and $_.Name -like '*.log' }`. Any
    other `{ }` script block, a `$variable` or `(...)` asks.
