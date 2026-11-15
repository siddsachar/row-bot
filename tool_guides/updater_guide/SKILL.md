---
name: updater_guide
display_name: Auto-Update Guide
icon: "⬆"
description: Is Row-Bot up to date? Checking for and installing Row-Bot updates.
tools:
  - row_bot_updater
tags: []
---
ROW-BOT AUTO-UPDATE:
- An installed Row-Bot checks GitHub Releases about once a day and, by
  default, downloads and prepares a new release in the background, next to
  the running version. Only what changed is downloaded and every file is
  checked. Nothing changes until Row-Bot restarts or quits.
- The person controls this in Settings › Updates: "Check for updates
  automatically" and "Download updates automatically" (both on by default),
  and the Stable or Beta channel. Do not change them without asking.
- A ready update shows "Restart to update" in the sidebar and the tray. If it
  is ignored, it installs the next time Row-Bot quits.

WHEN TO MENTION UPDATES:
- The injected dynamic state will include an "Update available: v…" line
  when one exists. If the person asks "what's new" or "is there an update",
  give the version and a one-line summary.
- Do NOT nag: mention it proactively at most once per conversation.
- If an update is in `skipped_versions`, do not offer it again unless the
  person asks.

CHECKING (row_bot_check_for_updates):
- Read-only. Asks GitHub once and returns the available version (if any),
  whether one is already downloaded and ready, the summary and the release
  page. Failures (offline, rate-limited) are reported, not treated as errors.

INSTALLING (row_bot_install_update):
- Follows the thread's approval mode; it always describes what it will do.
- With nothing ready: downloads and prepares the release in the background.
  Settings › Updates then shows Restart now.
- With an update ready: schedules the restart for as soon as nothing is
  running (after this reply). If the new version doesn't start, Row-Bot goes
  back to the current one by itself.
- "Needs the full installer" means the release changes Row-Bot's runtime:
  give the person the installer link it returns.
- Refuses on a development checkout. Docker and other host-updated installs
  have no update tools: their host installs updates.

GOING BACK:
- Settings › Updates › Repair ("Go back to x.y.z") restarts into the previous
  version. Point the person there if a new version misbehaves.

ERROR HANDLING:
- A checksum or preparation failure: do NOT retry blindly. Surface the
  message and point to Settings › Updates (Try again) or the release page.
- Network failure: a one-line "couldn't reach GitHub" suffices.
