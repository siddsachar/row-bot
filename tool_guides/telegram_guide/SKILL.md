---
name: telegram_guide
display_name: Telegram Guide
icon: "📱"
description: Guidance for Telegram messaging tools.
tools:
  - telegram
tags: []
---
TELEGRAM MESSAGING:
- You can send messages, photos, and documents to the user via Telegram
  using send_telegram_message, send_telegram_photo, send_telegram_document.
- All messages go to the configured Telegram user — no chat ID needed.
- Use send_telegram_message when the user asks you to 'send to my phone',
  'push this to Telegram', 'text me', or similar.
- Use send_telegram_photo to send images.
- To send files (CSV exports, PDFs, etc.), use send_file with to=telegram
  when it's available: it checks Telegram's size limit and shows the person
  what was sent. Otherwise use send_telegram_document.
- File paths can be workspace-relative (e.g. 'report.pdf') — the tool
  resolves them against the workspace folder automatically.
- These tools only work when the Telegram bot is running.
- FILE SENDING: When the user asks to 'export/generate X and send it',
  do BOTH steps automatically — generate the file first, then send it.
  Do not ask the user to specify a filename; pick a sensible name yourself.
  Workspace file: just pass the filename to send_telegram_document
  — paths resolve automatically.
