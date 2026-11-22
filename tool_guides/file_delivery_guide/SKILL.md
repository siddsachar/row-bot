---
name: file_delivery_guide
display_name: Send Files Guide
icon: "📤"
description: Show a finished file in this chat with present, once; send_file sends files to the person's devices or a chat app.
tools:
  - file_delivery
tags: []
---
- In a Row-Bot chat a finished video, image or file is shown with present;
  in a chat app's conversation, send_file delivers it. Don't present
  working material (frames, contact sheets, raw recordings you'll
  cut into the video). Once a file is presented, don't present or paste it
  again; send it to a device or app only when the person asks.
- send_file sends files the person already has: files in this chat by
  name, recordings (rec-…), video edits (edit-…) or workspace paths. Make
  or export the file first, then send it, in the same reply.
- "Send it to my phone / laptop / devices": to=devices. It shows in this
  chat (a video or picture plays there) on every device they're signed in
  to Row-Bot on, with a notice in each open Row-Bot window. The chat keeps
  it; only the link for their devices ends after 7 days. It never asks.
- When they name an app ("on Telegram", "to Slack"), use that app. It goes
  to their own chat there, or to this chat when the conversation came from
  that app; no other chat can be chosen. It may ask them first.
- If a file is too big for an app, or the app can't take files, say so in
  one line and offer to send it to their devices instead (the chat's card
  also has that button).
- At most 50 files in one reply.
