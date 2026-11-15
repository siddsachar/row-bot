"""Row-Bot as an MCP server for editors (E4): Claude Code, Cursor, Codex and Zed.

- ``catalog``: the editors, the three tools an editor sees and the words it gets back.
- ``store``: each editor's connection (a token in the keychain, only its hash in
  ``editors.db``), its last use, and the chats editors made or continued.
- ``service``: the tools, run inside the app: ``ask_row_bot`` (a turn in the chat for that
  editor and folder, at the chat's own level but never Hands-off), ``search_memory`` and
  ``list_conversations`` (both read-only).
- ``config_files``: the exact config snippet, and writing it into Claude Code's, Cursor's or
  Codex's own file once the person has seen the change (a backup first).
- ``routes``: the app's routes, Settings' and the bridge's (loopback only, with the token).
- ``bridge``: ``row-bot mcp``, the thin stdio process an editor starts (standard library only).
"""
