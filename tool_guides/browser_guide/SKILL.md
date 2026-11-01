---
name: browser_guide
display_name: Browser Guide
icon: "🌐"
description: Guidance for browser automation workflows.
tools:
  - browser
tags: []
inline: true
---
BROWSER AUTOMATION (Row-Bot's browser):
- Your browser tools drive Row-Bot's own browser. The person watches it live
  in the Browser pane beside the chat, stays signed in to sites there, and
  can take control at any time (your actions wait until they give it back).
- Every action returns the page after it: a short outline with refs like
  [ref=e12]. Act on refs from the latest result; a ref stays the same while
  the control does. One call is enough: there is no separate snapshot step.
- Workflow: browser_navigate → browser_click / browser_type /
  browser_select / browser_press → read the page in the result → repeat.
  browser_wait_for waits for text (a slow page) or, with nothing, returns
  the page as it is. browser_extract reads the page's text to answer from it.
- Pop-ups open as a new tab and become the active one; browser_tabs lists,
  switches and closes this chat's tabs.
- SIGNING IN: you never type a password, one-time code or card number.
  When a page asks for one, ask the person (ask_person) to sign in or enter
  it in the Browser pane, then carry on. Sign-ins last, so it's usually once.
- ASKING FIRST: paying, buying, booking with a charge, sending, posting,
  deleting and changing account settings ask before they run (by the chat's
  approval level). Do everything up to that step, then make the click; the
  person sees a card with the page.
- FILES: browser_upload sends files from this chat or the workspace through
  an upload control (it asks first). browser_download clicks a download
  and keeps the file in this chat's files.
- When the person says 'browse', 'open in the browser', or asks you to use a
  site (click, scroll, fill forms), use the browser_* tools. Use read_url
  only for raw text from a URL when they haven't mentioned the browser.
- LOCAL APPS: addresses on this computer (localhost:5173, 127.0.0.1:8000)
  open with browser_navigate once the person allows that address for this
  chat; it asks them once per address, and a dev server you started here
  with start_process opens without asking. Use the exact address the server
  printed. Other addresses on the local network (other computers, routers)
  never open; say so instead of retrying. Once allowed, a local app works
  as any site does, forms included.
- SCREENSHOTS: browser_screenshot shows the person a picture of the page in
  the chat (full_page=true for the whole page).
- PAGE SIZES: the page has the size of the person's Browser pane while it
  shows (a desktop window otherwise), so you both see the same layout.
  browser_viewport shows the page at phone, tablet or desktop size
  (device_scale 1-3 is optional); it stays until changed, and pane goes
  back to the pane's size.
- RECORDINGS: when the person asks for a video of what you do in the
  browser, call browser_record(action="start") first (it asks them), do
  the steps, then browser_record(action="stop"); the video is saved to the
  chat. It records only Row-Bot's browser and stops by itself after
  max_minutes. To record the whole screen use computer_record;
  if Computer use isn't available, tell them recording the screen needs it
  (Settings > System > Browser and computer use).
