---
name: calendar_guide
display_name: Calendar Guide
icon: "📅"
description: Guidance for Google Calendar and Outlook calendar events.
tools:
  - calendar
  - outlook_calendar
tags: []
---
CALENDAR TOOL:
- Each calendar has its own timezone setting. When creating or updating
  events, use the datetime format 'YYYY-MM-DD HH:MM:SS' (24-hour time).
- For day-level calendar reminders visible on all devices, prefer
  create_calendar_event over tasks.
- When listing events, the tool returns start/end times in the calendar's
  timezone. Convert to the user's timezone if different.
- Creating, changing, moving or deleting an event asks first. One with
  guests, or with send_updates set, reaches other people, so only the
  person approves it.

OUTLOOK CALENDAR:
- outlook_list_events and outlook_find_free_times take 'YYYY-MM-DD' (that
  whole day) or 'YYYY-MM-DD HH:MM', in the computer's time zone.
- Creating, changing, cancelling or answering an event asks first. An
  event with attendees sends them invitations and updates, so only the
  person approves it.
