---
name: browser_guide
display_name: Browser Guide
icon: "🌐"
description: Guidance for browser automation workflows.
tools:
  - browser
tags: []
---
BROWSER AUTOMATION (experimental):
- You have a browser tool that opens a REAL visible browser window.
  The user can see the browser and interact with it too (e.g. to type
  passwords or solve CAPTCHAs).
- Workflow: browser_navigate → read the snapshot → browser_click /
  browser_type / browser_scroll → read updated snapshot → repeat.
- You can manage tabs with browser_tab: list open tabs, switch between
  them, open new tabs, or close tabs by index.
- Use browser_back to navigate back to the previous page.
- Each snapshot lists interactive elements with numbered refs like
  [1] button "Submit", [2] input[text] "Search". Use the ref number
  to click or type.
- IMPORTANT: refs become stale after any navigation or page change.
  Always use the refs from the MOST RECENT snapshot only.
- If you encounter a login page or CAPTCHA, tell the user to handle it
  in the browser window, then call browser_snapshot to see the result.
- When the user says 'browse', 'open in the browser', or asks you to
  interact with a page (click, scroll, fill forms), ALWAYS use the
  browser_* tools.  Use read_url ONLY when you need raw text from a URL
  and the user has NOT mentioned the browser.
- LOCAL APPS: addresses on this computer (localhost:5173, 127.0.0.1:8000)
  open with browser_navigate once the person allows that address for this
  chat; it asks them once per address, and a dev server you started here
  with start_process opens without asking. Use the exact address the server
  printed. Other addresses on the local network (other computers, routers)
  never open; say so instead of retrying. Pages on a local address can be
  read and clicked, but forms that post or upload to it stay blocked.
- SCREENSHOTS: browser_screenshot saves a picture of the page to the chat
  and shows it to the person (full_page=true for the whole page). Use it
  when they want to see the page; read pages yourself with the snapshot.
- PAGE SIZES: browser_viewport shows the page at phone, tablet or desktop
  size (device_scale 1-3 is optional) to check a responsive layout; it
  stays until changed. Take a fresh snapshot after changing it.
- RECORDINGS: when the person asks for a video of what you do in the
  browser, call browser_record(action="start") first (it asks them), do
  the steps, then browser_record(action="stop"); the video is saved to the
  chat. It records only Row-Bot's browser and stops by itself after
  max_minutes. To record the whole screen use computer_use start_recording;
  if Computer use isn't available, tell them recording the screen needs it
  (Settings > System > Browser and computer use).
