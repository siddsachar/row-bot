"""The claim page (U1) and "This Row-Bot already has an owner" (U2), served at ``/setup``.

Server-rendered like ``/connect``: a browser without a session can't open the
React client. The page follows the client's palette, type and spacing, light and
dark by the browser's setting, at phone width too.
"""

from __future__ import annotations

from html import escape
import json
import math

from row_bot.access.setup_code import FAILURE_LIMIT, PAUSE_SECONDS
from row_bot.hosting import Host

PAUSE_MINUTES = PAUSE_SECONDS // 60

WELCOME = "Welcome to your Row-Bot"
OWNED = "This Row-Bot already has an owner"
OWNED_BODY = (
    "If it's yours, open it in a browser that's already signed in, "
    "or add this browser from Settings\u00a0›\u00a0Devices there."
)
CODE_LABEL = "Setup code"
CONTINUE = "Continue"
CHECKING = "Checking…"
STARTING = "Row-Bot is starting. This takes a few seconds."
WRONG = "That code doesn't match. Check it and try again."
RECOVERY = "Using this code signs out every other browser."
WHERE = "Where's my code?"
LOST = "Lost access?"
MANAGE = "Manage pod"
FAILED = "Row-Bot couldn't check the code. Try again."
NO_SCRIPT = "Turn on JavaScript in this browser to sign in."
# {time} is "14 minutes", "1 minute" or "40 seconds"; the page counts it down.
PAUSED = "Too many tries. Try again in {time}."


def lead(host: Host | None) -> str:
    if host is None:
        return "Enter the setup code to make this browser yours."
    return f"Enter the setup code from {host.name} to make this browser yours."


def where_help(host: Host | None) -> str:
    """Row-Bot prints the code to its log at every start while nobody owns it."""
    if host is None:
        return (
            "Row-Bot shows the setup code in its log when it starts. "
            'Look for \u201cRow-Bot setup code\u201d.'
        )
    return f"In {host.name}, open your pod's More\u00a0›\u00a0Show Logs and copy the setup code."


def lost_help(host: Host | None) -> str:
    if host is None:
        return (
            "Give this Row-Bot a new setup code in its server settings and restart it, "
            "then enter the new code here."
        )
    return (
        "Change the setup code in your pod's settings and restart it, "
        "then enter the new code here."
    )


def used_message(host: Host | None) -> str:
    if host is None:
        return (
            "This code has already been used or has expired. To make a new one, give "
            "this Row-Bot a new setup code in its server settings and restart it."
        )
    return (
        "This code has already been used or has expired. To make a new one, change "
        "the setup code in your pod's settings and restart it."
    )


def tries_left_message(tries_left: int) -> str:
    tries = "1 try" if tries_left == 1 else f"{tries_left} tries"
    return f"{WRONG} {tries} left before a {PAUSE_MINUTES}-minute pause."


def _duration(seconds: int) -> str:
    if seconds >= 60:
        minutes = math.ceil(seconds / 60)
        return "1 minute" if minutes == 1 else f"{minutes} minutes"
    return "1 second" if seconds == 1 else f"{seconds} seconds"


def paused_message(seconds: int) -> str:
    return PAUSED.format(time=_duration(seconds))


_ALERT_ICON = (
    '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.25" '
    'fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 4.75v4" '
    'stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><circle cx="8" '
    'cy="11.1" r=".9" fill="currentColor"/></svg>'
)
_EXTERNAL_ICON = (
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M9.5 2.75h3.75v3.75M13 3 '
    '7.5 8.5M11.5 9.5v3a.75.75 0 0 1-.75.75h-7.5a.75.75 0 0 1-.75-.75v-7.5a.75.75 0 0 '
    '1 .75-.75h3" fill="none" stroke="currentColor" stroke-width="1.5" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
_CHEVRON_ICON = (
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3.5 4.5 4.5L6 12.5" '
    'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
    'stroke-linejoin="round"/></svg>'
)

_STYLE = """
:root {
  color-scheme: light dark;
  --canvas: #F3F5F7; --surface: #FFFFFF; --sunken: #ECEFF2; --hover: #ECEFF3;
  --text: #141922; --text-2: #3A4351; --muted: #535D6C;
  --border: #DDE2E8; --hairline: #0F172A14; --control: #6B7686;
  --accent: #285E8A; --on-accent: #FFFFFF;
  --danger: #8B2735; --danger-bg: #FBEAEC; --warn: #704709; --warn-bg: #FFF1CF;
  --shadow: 0 1px 2px -1px #0F172A1F, 0 12px 32px -18px #0F172A40;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --canvas: #0B0E13; --surface: #11151C; --sunken: #080A0E; --hover: #212834;
    --text: #E8EBF0; --text-2: #BCC4CF; --muted: #98A2B1;
    --border: #252C37; --hairline: #FFFFFF12; --control: #7D8899;
    --accent: #78B8F2; --on-accent: #0B0E13;
    --danger: #F4AFB8; --danger-bg: #491F29; --warn: #F4D48D; --warn-bg: #443414;
    --shadow: inset 0 1px 0 #FFFFFF0D, 0 16px 40px -16px #00000099;
  }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { margin: 0; }
body {
  min-height: 100vh; min-height: 100dvh; display: flex; align-items: center; justify-content: center;
  padding: 32px 16px; background: var(--canvas); color: var(--text);
  font-size: 14px; line-height: 21px; -webkit-font-smoothing: antialiased; -webkit-text-size-adjust: 100%;
}
main {
  width: 100%; max-width: 420px; padding: 32px; border: 1px solid var(--hairline);
  border-radius: 16px; background: var(--surface); box-shadow: var(--shadow);
}
.glyph { display: block; width: 56px; height: 56px; margin: 0 auto 20px; }
h1 { margin: 0; text-align: center; font-size: 26px; line-height: 32px; font-weight: 600; letter-spacing: -0.01em; text-wrap: balance; }
.lead { margin: 8px 0 0; text-align: center; color: var(--text-2); font-size: 15px; line-height: 24px; text-wrap: balance; }
.status { display: flex; align-items: center; justify-content: center; gap: 10px; margin: 24px 0 0; color: var(--text-2); font-size: 15px; line-height: 24px; text-align: center; }
.code-form { display: grid; gap: 8px; margin-top: 24px; }
label { color: var(--text-2); font-size: 13px; line-height: 20px; font-weight: 500; }
input {
  width: 100%; height: 48px; padding: 0 14px; border: 1px solid var(--control); border-radius: 8px;
  background: var(--surface); color: var(--text);
  font: 500 17px/24px ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  letter-spacing: 0.08em; text-transform: uppercase;
}
input::placeholder { color: var(--muted); opacity: 1; }
input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
input[aria-invalid="true"] { border-color: var(--danger); outline-color: var(--danger); }
input:read-only, input:disabled { background: var(--sunken); color: var(--muted); }
.message, .note { display: flex; gap: 8px; align-items: flex-start; margin: 0; padding: 10px 12px; border-radius: 10px; font-size: 14px; line-height: 21px; }
/* The consequence of Continue stays next to it, quieter than a message. */
.note { padding: 2px 0; color: var(--text-2); }
.note svg { color: var(--warn); }
.message svg, .note svg { flex: none; width: 16px; height: 16px; margin-top: 2.5px; }
.error { background: var(--danger-bg); color: var(--danger); }
.warn { background: var(--warn-bg); color: var(--warn); }
button.primary {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px; height: 44px; margin-top: 8px;
  border: 0; border-radius: 8px; background: var(--accent); color: var(--on-accent);
  font: inherit; font-size: 15px; font-weight: 600; cursor: pointer;
}
button.primary:hover:not(:disabled) { filter: brightness(1.08); }
button.primary:active:not(:disabled) { filter: brightness(0.95); }
button.primary:disabled { cursor: default; opacity: 0.6; }
button.primary[aria-busy="true"] { opacity: 1; }
button:focus-visible, a:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.secondary {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 40px; padding: 0 14px;
  border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: transparent;
  font-weight: 500; text-decoration: none; justify-self: start;
}
.secondary:hover { background: var(--hover); }
.secondary svg { width: 14px; height: 14px; color: var(--muted); }
details { margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--border); }
summary {
  display: inline-flex; align-items: center; gap: 4px; color: var(--accent); font-weight: 500;
  cursor: pointer; list-style: none; border-radius: 6px;
}
summary::-webkit-details-marker { display: none; }
summary svg { width: 14px; height: 14px; transition: transform 120ms cubic-bezier(0.2, 0, 0, 1); }
details[open] summary svg { transform: rotate(90deg); }
details > p { margin: 8px 0 0; color: var(--text-2); }
details > .secondary { margin-top: 12px; }
details .code-form { margin-top: 16px; }
.spinner {
  flex: none; width: 16px; height: 16px; border: 2px solid currentColor; border-right-color: transparent;
  border-radius: 50%; animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .spinner { animation-duration: 2.4s; }
  summary svg { transition: none; }
}
@media (max-width: 480px) {
  body { align-items: flex-start; padding-top: 24px; }
  main { padding: 28px 20px 24px; }
  h1 { font-size: 22px; line-height: 28px; }
}
"""

_SCRIPT = """
(() => {
  const state = JSON.parse(document.getElementById('setup-state').textContent);
  const words = state.words;
  const $ = (id) => document.getElementById(id);
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const unavailable = (status) => status >= 502 && status <= 504;
  const duration = (seconds) => {
    if (seconds >= 60) {
      const minutes = Math.ceil(seconds / 60);
      return minutes === 1 ? words.minute : words.minutes.replace('{n}', minutes);
    }
    return seconds === 1 ? words.second : words.seconds.replace('{n}', seconds);
  };
  function enter() {
    // The client says "This browser is signed in." once it opens.
    try { sessionStorage.setItem('row-bot:signed-in', '1'); } catch (_) {}
    location.replace('/app-v2/');
  }
  async function signedIn() {
    try {
      const answer = await fetch('/api/access/session', { cache: 'no-store', credentials: 'same-origin' });
      return (await answer.json()).authenticated === true;
    } catch (_) { return false; }
  }

  function flow(prefix, around) {
    const form = $(prefix + '-form');
    const input = $(prefix + '-code');
    const button = $(prefix + '-continue');
    const message = $(prefix + '-message');
    const manage = $(prefix + '-manage');
    const status = $(prefix + '-status');
    let timer = 0;
    function starting(on) {
      status.hidden = !on;
      for (const element of [form, ...around]) element.hidden = on;
    }
    function say(text, tone) {
      message.querySelector('.message-text').textContent = text;
      message.className = 'message ' + tone;
      message.hidden = false;
      input.setAttribute('aria-invalid', tone === 'error' ? 'true' : 'false');
    }
    function quiet() {
      message.hidden = true;
      input.removeAttribute('aria-invalid');
      if (manage) manage.hidden = true;
    }
    function busy(on) {
      input.readOnly = on;
      button.disabled = on;
      button.setAttribute('aria-busy', String(on));
      button.querySelector('.spinner').hidden = !on;
      button.querySelector('.label').textContent = on ? words.checking : words.continue;
    }
    function pause(seconds) {
      clearInterval(timer);
      const until = Date.now() + seconds * 1000;
      input.disabled = true;
      button.disabled = true;
      const tick = () => {
        const left = Math.ceil((until - Date.now()) / 1000);
        if (left > 0) { say(words.paused.replace('{time}', duration(left)), 'warn'); return; }
        clearInterval(timer);
        input.disabled = false;
        button.disabled = false;
        quiet();
      };
      tick();
      timer = setInterval(tick, 1000);
    }
    async function untilReady() {
      for (let shown = false; ; shown = true) {
        let down;
        try { down = unavailable((await fetch('/readyz', { cache: 'no-store' })).status); }
        catch (_) { down = true; }
        if (!down) { if (shown) starting(false); return; }
        if (!shown) starting(true);
        await wait(2000);
      }
    }
    async function submit(code) {
      quiet();
      busy(true);
      let answer = null;
      let body = {};
      try {
        answer = await fetch('/api/access/setup', {
          method: 'POST',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ code }),
        });
        body = await answer.json().catch(() => ({}));
      } catch (_) { answer = null; }
      if (!answer || unavailable(answer.status)) {
        // Row-Bot is restarting: wait, and don't spend the code twice if it already signed in.
        await untilReady();
        if (await signedIn()) { enter(); return; }
        return submit(code);
      }
      if (answer.ok && body.ok) { enter(); return; }
      busy(false);
      if (answer.status === 429) {
        pause(Number(body.retry_after || answer.headers.get('Retry-After')) || 60);
        return;
      }
      if (body.error === 'code_used') {
        say(body.detail || words.failed, 'warn');
        if (manage) manage.hidden = false;
        return;
      }
      say(body.detail || words.failed, 'error');
      input.focus();
      input.select();
    }
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!input.value.trim()) { input.focus(); return; }
      submit(input.value.trim());
    });
    input.addEventListener('input', () => { if (!input.disabled) quiet(); });
    return { input, pause, untilReady };
  }

  if (state.owned) {
    const lost = flow('lost', []);
    if (state.paused) lost.pause(state.paused);
    return;
  }
  const claim = flow('claim', [$('claim-lead'), $('where')]);
  claim.untilReady().then(() => {
    if (state.paused) claim.pause(state.paused);
    else claim.input.focus();
  });
})();
"""


def _form(prefix: str, *, recovery: bool, manage: str) -> str:
    note = (
        f'<p class="note">{_ALERT_ICON}<span>{escape(RECOVERY)}</span></p>'
        if recovery
        else ""
    )
    return f"""
<p class="status" id="{prefix}-status" role="status" aria-live="polite" hidden><span class="spinner" aria-hidden="true"></span><span>{escape(STARTING)}</span></p>
<form class="code-form" id="{prefix}-form" novalidate>
  <label for="{prefix}-code">{escape(CODE_LABEL)}</label>
  <input id="{prefix}-code" name="code" type="text" inputmode="text" autocomplete="one-time-code" autocapitalize="characters" autocorrect="off" spellcheck="false" maxlength="80" placeholder="XXXX-XXXX-XXXX" aria-describedby="{prefix}-message">
  <p class="message" id="{prefix}-message" role="alert" hidden>{_ALERT_ICON}<span class="message-text"></span></p>
  {manage}
  {note}
  <button class="primary" id="{prefix}-continue" type="submit"><span class="spinner" aria-hidden="true" hidden></span><span class="label">{escape(CONTINUE)}</span></button>
</form>"""


def _manage_link(host: Host | None, element_id: str, *, hidden: bool) -> str:
    if host is None or not host.manage_url:
        return ""
    tooltip = f"Opens {host.name} in a new tab"
    return (
        f'<a class="secondary" id="{element_id}" href="{escape(host.manage_url, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer" title="{escape(tooltip, quote=True)}"'
        f'{" hidden" if hidden else ""}>{escape(MANAGE)}{_EXTERNAL_ICON}</a>'
    )


def render_setup_page(
    *,
    owned: bool,
    host: Host | None,
    paused_seconds: int,
) -> str:
    """The whole page. Both views are present; the script shows each state."""
    words = {
        "continue": CONTINUE,
        "checking": CHECKING,
        "failed": FAILED,
        "paused": PAUSED,
        "minute": "1 minute",
        "minutes": "{n} minutes",
        "second": "1 second",
        "seconds": "{n} seconds",
    }
    state = json.dumps(
        {
            "owned": owned,
            "paused": max(0, int(paused_seconds)),
            "words": words,
        },
        ensure_ascii=True,
    ).replace("<", "\\u003c").replace(">", "\\u003e")
    claim = f"""
<section id="claim"{" hidden" if owned else ""}>
  <h1>{escape(WELCOME)}</h1>
  <p class="lead" id="claim-lead">{escape(lead(host))}</p>
  {_form("claim", recovery=False, manage=_manage_link(host, "claim-manage", hidden=True))}
  <details id="where">
    <summary>{escape(WHERE)}{_CHEVRON_ICON}</summary>
    <p>{escape(where_help(host))}</p>
  </details>
</section>"""
    owner = f"""
<section id="owned"{"" if owned else " hidden"}>
  <h1>{escape(OWNED)}</h1>
  <p class="lead">{escape(OWNED_BODY)}</p>
  <details id="lost">
    <summary>{escape(LOST)}{_CHEVRON_ICON}</summary>
    <p>{escape(lost_help(host))}</p>
    {_manage_link(host, "lost-host", hidden=False)}
    {_form("lost", recovery=True, manage="")}
  </details>
</section>"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="light dark">
  <title>Row-Bot</title>
  <link rel="icon" href="/static/row_bot_glyph_256.png">
  <style>{_STYLE}</style>
</head>
<body>
<main>
  <img class="glyph" src="/static/row_bot_glyph_256.png" alt="" width="56" height="56">
  {claim}
  {owner}
  <noscript><p class="lead">{escape(NO_SCRIPT)}</p></noscript>
</main>
<script id="setup-state" type="application/json">{state}</script>
<script>{_SCRIPT}</script>
</body>
</html>"""


__all__ = [
    "FAILURE_LIMIT",
    "paused_message",
    "render_setup_page",
    "tries_left_message",
    "used_message",
]
