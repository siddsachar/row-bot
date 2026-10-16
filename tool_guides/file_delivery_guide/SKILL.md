---
name: file_delivery_guide
display_name: Send Files Guide
icon: "📤"
description: Guidance for sending files to the person's devices or a chat app with send_file.
tools:
  - file_delivery
tags: []
---
- send_file sends files the person already has: files in this chat by
  name, recordings (rec-…), video edits (edit-…) or workspace paths. Make
  or export the file first, then send it, in the same reply.
- "Send it to my phone / laptop / devices": to=devices. It shows in this
  chat as a download on every device they're signed in to Row-Bot on, with
  a notice in each open Row-Bot window, for 7 days. It never asks.
- When they name an app ("on Telegram", "to Slack"), use that app. It goes
  to their own chat there, or to this chat when the conversation came from
  that app; no other chat can be chosen. It may ask them first.
- If a file is too big for an app, or the app can't take files, say so in
  one line and offer to send it to their devices instead (the chat's card
  also has that button).
- At most 10 files in one reply.
