---
name: filesystem_guide
display_name: Filesystem Guide
icon: "📁"
description: Guidance for workspace file tools vs shell commands.
tools:
  - filesystem
tags: []
---
- FILESYSTEM vs SHELL ROUTING:
  * The workspace_* file tools (workspace_read_file, workspace_find,
    workspace_write_file, workspace_copy_file, workspace_move_file,
    workspace_file_delete) take paths relative to the workspace folder. A full
    path elsewhere on this computer works too: the tool asks the person first
    (reading says so on its card), and Row-Bot's own data folder is always
    refused.
  * workspace_find lists a folder when pattern is empty, and finds files
    beneath it by a name pattern ('*.pdf', 'report*') when it is given.
  * workspace_read_file doesn't show an image or read what's in it: look at
    one with analyze_image (source='file'), and show a finished one to the
    person with present.
  * For a terminal command (python --version, git status, pip install, etc.)
    or any other system operation, use run_command.
  * NEVER tell the user to open a terminal or run a command themselves.
    You have run_command — use it directly.
- ORDINARY SAVES AND CONVERSATIONAL CONFIRMATION:
  * Creating, writing, copying, and exporting ordinary workspace files are not
    approval-gated operations. Use the relevant workspace tool directly when
    the user asks to save without requesting a separate confirmation.
  * If the user explicitly says to ask before saving, treat that as normal
    conversation, not a protected-action approval: finish and present the
    material, ask for confirmation, and make zero write/copy/export calls on
    that turn. After a later affirmative user message, make exactly one normal
    write/copy/export call and report success only after the tool succeeds.
  * Do not create an approval request or promise an approval card for an
    ordinary workspace save. Move and delete operations keep their existing
    configured approval behavior.
