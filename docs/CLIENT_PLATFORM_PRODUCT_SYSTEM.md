# Shared client product system

The default React client uses a single semantic product system. Domain features consume
`frontend/src/ui`; they do not introduce separate palettes, overlay stacks or
resizer implementations. The conversation stays mounted under route, layout and
appearance changes.

## Theme contract

`theme-model.ts` owns version 1 preferences and all palette values. Appearance
is `system`, `light` or `dark`; accents are blue, teal, violet and amber. Density
is comfortable or compact. The local `row-bot.appearance.v1` key contains only
appearance preferences, never backend state or secrets. Version 0 values are
normalized, unknown versions or malformed values reset safely, and unavailable
storage leaves usable session preferences.

The same self-contained `bootstrapTheme` runs inline before the first stylesheet
and in the React provider. It writes semantic CSS variables and root attributes;
the host computes an exact CSP hash for the verified inline script. System colour
and transparency listeners are removed with the provider. Storage events update
other open clients. Features use `useTheme().update`, never write palette values
or this storage key independently.

Elevation is expressed by luminance, not borders: `--canvas` → `--surface` →
`--surface-raised` → `--surface-overlay` (menus, popovers, dialogs, drawers). In
dark mode each step is lighter; in light mode chrome sits on a slightly darker
canvas and the overlay is the lightest layer. Neutrals carry a slight cool bias
toward the blue accent. Use `--border-hairline` (about 7% alpha) only where tone
alone cannot separate content; `--border-subtle`/`--border-control` remain for
inputs and controls that need a visible 3:1 boundary. Also use hover/pressed/
disabled surfaces; primary/secondary/muted/inverse text; accent/focus/link/
selection variables; named status, code/syntax, diff, chart and artifact tokens.
Status and diff meaning always includes text or a marker. Do not infer meaning
from an accent alone. The theme contract tests check text at 4.5:1, essential
non-text controls at 3:1, elevation ordering and hairline alpha; browser tests
verify composited results too.

Non-palette tokens live in `src/ui/styles/tokens.css`. The stylesheet entry
`src/ui/styles/index.css` fixes the cascade: bundled fonts, tokens, the legacy
surface rules in `styles.css` (split into per-surface files as surfaces are
reworked), then shared primitives, then reworked surfaces (`chat.css`). Geist
and Geist Mono are bundled locally (`--font-sans`, `--font-mono`) with a system
fallback stack. Syntax colours are `--syntax-keyword`, `--syntax-string`,
`--syntax-number`, `--syntax-function` and `--code-comment` on
`--code-background`; `chat.css` maps Shiki's CSS-variable theme onto them.

The type scale is 12 / 13 / 14 / 15 / 17 / 20 / 26 (`--text-*` with paired
`--leading-*`), weights 400/500/600, and tabular numbers for data. Dense chrome
uses the 14/21 body; the conversation reads at 15/24 (`--type-size-reading`);
labels are 13/20, metadata 12/18 and code 13/20. Comfortable and touch controls
are at least 44px; compact desktop fine-pointer controls may be 32px and icon
actions 28/32px. The space scale is 4/8/12/16/24/32/48px; control/panel/dialog
radii are 6/12/16px. Motion tokens are 120ms for popovers (`--motion-popover`)
and 180ms for panels (`--motion-panel`) with `--ease-out`; reduced motion sets
both to zero and removes animations. Background transitions do not animate
theme colours through intermediate low-contrast values.

Focus uses a visible 2px ring and offset for keyboard focus. Programmatic focus
targets (`tabindex="-1"` route headings, landmark regions, transcript messages)
receive focus for assistive technology but draw the ring only when
`data-input-modality="keyboard"` is set on the root by `installInputModality`.

### Appearance (light theme and OS default)

A device that has never chosen an appearance follows the operating system:
the default preference is `system`, resolved through `prefers-color-scheme`
before the first stylesheet and again whenever the system changes. Choosing
Light or Dark in Settings › Appearance stores that choice per device and it
wins over the system until the person picks System again. Dark stays the
showcase for captures and marketing; both themes are first-class.

Light and dark share every semantic token. Surfaces separate by tone in both
(light chrome sits on a slightly darker canvas, the overlay layer is the
lightest), text keeps 4.5:1 and essential non-text 3:1, and status is always
a shape plus a word. Canvas visuals that cannot read CSS variables directly
(the knowledge graph, charts, design previews) take per-theme values from the
token model. Verify a visual change in light and dark, and with System under
both OS schemes.

## Public primitive inventory

| Surface | Contribution contract |
| --- | --- |
| `Button`, `Input`, `Select`, `Field` | Native semantics and labels; primary/secondary/ghost/danger actions; disabled states and named icon buttons. Select stays a native browser control. |
| `Tabs`, `Menu`, `Popup`, `Hint` | Radix owns keyboard, focus and dismissal behavior. Floating surfaces layer above their active task; give each trigger an accessible name. Menus are bounded by Radix's available width/height and scroll internally, revealing the current choice; `Hint` accepts an optional `shortcut`. Menu actions may carry a 16px monochrome `icon`, a keycap `shortcut` (announced through `aria-keyshortcuts`, kept out of the item's name) and `separatorBefore`; destructive actions always render last, in red, after a separator. An action with `afterClose` runs once the menu has released focus instead of returning focus to the trigger (for items that focus a field they open, such as Rename). Only one transient popover shows at a time: composer-owned popovers such as the slash palette step aside while focus is in another control. |
| `OverlayProvider`, `useOverlay` | One Radix modal scope with title/description; dialogs, short sheets, navigation drawers, the command palette (`kind: 'palette'`: no header or footer chrome, title kept for assistive tech) and alert-dialog semantics share it. |
| Notifications | `notify` coalesces duplicate text and retains at most three notices. They float just below the top bar (centred, 420px; full width on phones), take no room in the layout and never cover the composer, and go away by themselves: 5 s, warnings and errors 8 s, with an action such as Undo 12 s; hovering or focusing one holds it. Notices wait while a modal is open, so they cannot cover its footer or consume Escape. Errors also need a persistent inline recovery action. |
| `Skeleton`, `EmptyState`, `ErrorState`, `Progress` | Name the operation; delay skeleton visuals 150ms with cancellation; never invent percentage progress. Empty states explain a useful next step. |
| `Surface` | Opaque by default. The optional elevated effect has a 94% overlay backing and bounded blur only with supporting CSS and appropriate preferences. |
| `IconButton`, `Kbd` | Icons for verbs: 28px (`sm`) or 32px (`md`) on fine pointers and 44px on touch. The `label` is required and is both the accessible name and the tooltip; an optional `shortcut` such as `Mod+K` renders keycaps (⌘ on macOS, Ctrl elsewhere) and sets `aria-keyshortcuts`. When a text button becomes an icon button, keep its accessible name. |
| `ProgressRing` | Counted progress as a ring with the count inside ("4/11"); `role="progressbar"` with the label as its value text. Turns to the success tone when complete. |
| `CopyGlyph`, `useCopyFeedback` | A copy action's icon becomes a check drawn in one stroke for 1.6s after a successful copy; the resting style is the finished glyph. |
| `StatusDot` | Status as shape, then word. The label is always present, visually hidden unless `showLabel`. Tones: neutral, accent, info, success, warning, danger. |
| `Segmented` | Single-choice radio group with one tab stop and arrow/Home/End keys that skip disabled options. Icon-only options keep their label as accessible name and tooltip. |
| `Disclosure` | Native `details`/`summary` with a rotating chevron and optional meta, for "Advanced" sections and quiet rail groups. Pass a plain string summary so the summary text stays queryable. |
| `SettingRow`, `Field` | Label and help on the left, one control on the right; a labelled/described group. `htmlFor` ties the visible label to a native control; `modified` shows an accent dot. `Field layout="row"` gives a saved field the same row: the label names the control by id and the hint becomes its description. `Toggle` is a switch with no On/Off text. |
| `EntityList`, `EntityRow` | Logo, name, status plus meta, one primary action, a named ⋯ menu (`More actions for …`) and optional inline details behind an expand control. |
| `StatGroup`, `Stat` | A definition list of metrics with tabular values, optional unit and toned delta. |
| `InlineEmpty` | A one-line muted empty state with an optional action for dense sections. Prefer hiding an empty section entirely when nothing is actionable. |
| `Combobox` | Searchable single-choice picker for large sets (models, conversations): trigger named by `label` and described by the current value, a `combobox` input with `aria-activedescendant`, grouped `listbox` options, disabled options skipped, Enter chooses and focus returns to the trigger. Native `Select` stays for short enums. |
| `Toolbar`, `ToolbarSeparator` | `role="toolbar"` with arrow/Home/End focus movement; `floating` adds the glass canvas treatment with placements. Segmented groups and text fields keep their own keys. |
| `Drawer` | Side inspector on the overlay layer. Non-modal by default: the canvas stays interactive, focus moves to the drawer heading and Escape or Close dismisses it. `container` renders it inside a positioned surface; `modal` adds the scrim and focus trap. Becomes a bottom sheet on phones. |
| Workspace commands | A labeled button at every width and Ctrl/Cmd+K open the palette: one searchbox (`Find a workspace command`) driving a grouped `listbox` through `aria-activedescendant` across conversations, commands, settings pages and agents, plus a debounced full-text history search with snippets. Scattered letter matches show only when nothing matches literally; the group holding the best match comes first so Enter runs it. Mod+Shift+O starts a new chat (browsers reserve Mod+N) and Mod+. toggles the right region. Modified/reserved chords and IME composition pass through. |
| Pane groups | `react-resizable-panels` supplies pointer/touch capture and separator semantics; the typed layout model owns bounds/persistence. |

Do not mount a domain modal inside another modal. `open` replaces a task;
confirmations suspend the still-mounted task content, hide/inert it, and restore
its values and focus on Cancel/Escape/outside click. Only the active task owns a
focus trap and body scroll lock. Confirmation defaults to Cancel; unrelated text
Enter never confirms. The footer remains visible while the body scrolls.
Primary-pointer buttons establish the opener focus even in WebKit, while
respecting compound primitives that prevent the pointer event. Header controls
wrap when zoom or available width requires it; clipping is not a responsive mode.
Menu actions receive their stable trigger for `returnFocusTo` when opening a
task, since the selected menu item disappears. Closing, moving or collapsing a
dock restores a surviving tab or the Open panel trigger. Commands read the
current layout when invoked, including after an open command surface changes
breakpoint.

Desktop and tablet dialogs are bounded to 560px and viewport minus 48px (the
palette to 640px); on tablets only large tasks (the workflow builder, an
expanded chart) take the whole screen, and phones use a full task surface with
the footer at the bottom edge. Short sheets use at most 85dvh (Context is a
full-height sheet on phones and a side sheet on tablets); navigation uses a
280px tablet drawer and a full-screen phone list, both closed from the header. Overlay keys allow an owner
to dismiss its responsive surface without dismissing an unrelated task.
A closing surface keeps its presentation and unmounts at once (a changed
animation would keep it mounted, dismissable, for the next tap). Anything
that navigates from inside an overlay navigates first and closes second, so
the overlay's history step cannot undo the new route.
Fixed surfaces also respect their containing viewport at zoom; the header text
can wrap and the body scrolls within the height left by the header and footer.
Verify loaded controls and actual viewport hit targets, since a full-page
screenshot can include controls that a scroll-locked user cannot reach.

## Layout and settings contributions

The sidebar is one tone darker than the main surface, which has no frame or
gutter; splitters are hairlines with a widened hit target. Its header holds the
logo and icon actions (New chat, Workspace commands, collapse); Home and Agents
are the only destination rows; the footer holds Buddy (a 44px avatar in an
activity ring, name and status, whose button opens Buddy settings) and the
Settings gear. The collapsed
48px rail keeps expand, commands, New chat, Home, Agents, Settings and Buddy as
labelled icons. A compact icon `Segmented` filters conversations by the server
`category` (All, Chats, Designs, Code, Workflows) and persists per device.
Rows keep server order; Pinned comes first, and a Today / Yesterday / This week
/ Older label starts each recency run inside the `Recent conversations` list, so
the list still holds one item per conversation. The open conversation stays
listed when the preview hides it, inserted where the order puts it, but only if
it matches the type filter. Rows show a monochrome type
glyph, a short time, and pin/⋯ over the time on hover or focus.

The sidebar previews ten conversations in server order. Show more
reveals the loaded page; Load more keeps the existing cursor continuation. Show
less and section collapse retain the current conversation, including a confirmed
selection outside the loaded page. Expansion is session presentation state and
does not introduce another selection or persistence owner. Activating a row also
returns from gallery/settings routes to the conversation. Titles truncate with
ellipsis while keeping their full accessible name and hover/focus hint. Compact
selection also reveals the conversation through the existing layout focus owner;
panel registrations remain available to reopen and desktop docks stay in place.
Ordinary root-view selections preserve the current browser history and query.
Compact fine-pointer desktop rows may be 34px; comfortable and touch rows stay 44px.
The conversation library (`/library`) holds full-text search with snippets, the
type filter and bulk actions; quick switching belongs to the sidebar and palette.

Context is a small floating glass card: content height, rounded, with a
frosted translucent fill that turns opaque under reduced transparency, forced
colours or missing backdrop-filter support. It renders once, through a portal,
into a persistent host element, so opening, closing or collapsing a panel never
remounts it or re-reads its sources. A wide chat gives the card a column of its
own (hidden only on request, remembered per device); when a panel narrows the
chat, the header `Context` button or Mod+. floats it over the conversation and
Escape or an outside click closes it; compact layouts use a sheet that adopts
the same mounted host through a `ContextSlot`, so it stays live while open.
Design,
Workspace and Terminal panels keep the full-height right region, labelled
`Side panels` only while it holds panels. Exactly one `Close all panels` is
visible: in the side region, else the bottom region, else a floating rail that
lists panels whose region is hidden. Open panel lives in the conversation header
on desktop and in the compact controls below 1024px. Live delegated agents open
and promote Context's Agents section by CSS order, never by remounting it. A
delegated agent that is still going has Stop in place on its row and Message
and Stop in its detail beside Open full thread; inside its own thread Agents
shows that agent with its status and the same controls above Back to parent
conversation. Statuses read in words (Working, Waiting for approval, Needs
you, Done, Stopped).

The goal card in Context follows what the conversation is doing: Working only
while one of its turns runs, Continuing between turns, Waiting for your
approval, Paused, Needs you, Done or Stopped. It shows "Turn 3 of 10" (the
turn under way while working), the verifier's latest reason, and Pause/Resume
and Stop (Stop ends the goal and the turn it is running; no dialog). Starting a
goal works at once and the server continues it after each completed turn up to
its limit (default 10); approvals, Stop and failures pause it.

A conversation's type comes from its bindings as well as the server's single
category, so a unified thread holding a design and a code folder matches both
the Designs and the Code filters (and the palette's "design"/"code" words);
Chats are threads with neither. Its glyph shows the first kind with the second
as a small badge.

Tooltip portals use a noninteractive, transformed viewport layer so floating
placement can measure the containing block's scale at page zoom. Content uses
the primitive's available-width measurement and keeps pointer access; the layer
must not intercept underlying controls. Browser regressions verify actual full
title bounds and focus, including narrow WebKit at CSS zoom 2.

The conversation area has a 400px desktop minimum. Navigation defaults to 240px
with 200–320px bounds and a 48px collapsed rail. Side and bottom bounds are in
the panel model, not copied into domain CSS. Keyboard arrows move 16px, Shift
moves 48px, Home/End reach bounds and Enter collapses/restores. Menus provide
resize/move/collapse actions without dragging. Compact resource views become
registered tabs or sheets while preserving instance identity.

Mount a library `Separator` only while its resize surface is available. Keep
the surrounding groups and panels mounted. Hiding a registered separator with
`display: none` prevents the library's geometry-based panel association from
supplying its ARIA control/range values when it later appears. Verify numeric
min/current/max and a live controlled panel after open, resize, restore and
breakpoint changes. Keyboard collapse returns focus to the Open panel trigger
when the side or bottom handle disappears.
Group resize completion also reconciles the library's 48px navigation or 0px
side/bottom drag-collapse sizes into the versioned model, preserving the last
expanded restore size. Test pointer collapse across refresh and restore; a
visually collapsed pane alone does not prove persisted state is correct.

### Conversation surface

The conversation is airy; tools inside it stay dense (phones and tablets:
see Responsive below). The transcript and the
composer share one centred column (`--chat-column`, 760px, with a 24px gutter;
12px under 768px) and prose is capped at `--prose-width` (72ch) on the 15/24
reading type. The header is one 48px row: the title (rename in place), a
read-only model chip and icon actions (Find, Share or export, Context on
compact layouts). Older rows load above the live window when the reader reaches
the top ("Earlier messages" also works as a button) with the first visible row
held in place; a floating "Latest messages" pill (`↓ N new`) returns to the live
end. Loaded history sits in an `aria-live="off"` wrapper inside the log.

- User turns are right-aligned bubbles (`--surface-raised`, at most 78% of the
  column). Assistant turns are unlabeled prose; consecutive assistant rows sit
  8px apart and read as one turn. Actions appear once per turn: beside the user
  bubble, and under the last row of an assistant turn (always visible on the
  newest reply), copying the whole turn. Read aloud uses only on-device voices.
- Tool calls are one activity row per turn ("Used 3 tools · 1 failed") with tool
  glyphs; it expands into a step timeline with human verbs and the key
  argument, and step details load their paged result on demand. Live work shows
  one activity row with gently pulsing text (opacity only); an approval card anchors to it and is
  never hidden. A step that failed or never ran is never worded as done
  ("Couldn't read a file", "Didn't delete a file"; "1 tool skipped"). Runtime
  errors and interrupted runs are callouts with the cause and a next step
  (Resume, Retry, Switch model, Open providers, New chat); a stop that leaves
  the last message unanswered offers Send again.
- A design or code folder the assistant creates (`create_design`,
  `create_code_folder`) is a card in its turn: "Created design Harbour cleanup
  deck" with Open, Rename (in place) and Undo; once undone it reads "Removed
  …". A tool the work needs is a setup card ("Turn on Web Search?" with Turn on
  / Not now) instead of a generic approval, and an account or channel it needs
  is a Connect card that opens its Settings page. Nothing is created from a
  message's wording, and a design opens by itself only when a turn changed it.
- While Row-Bot uses the computer (Cua) in a conversation, a computer-use
  card sits in the turn: "Using your computer · <app>" with a status dot, the
  latest picture (in memory only, read by revision, `no-store`, hidden while
  paused or waiting for approval, with a line saying why), and Pause ("you
  take over"), Resume and Stop. A paused turn shows the card instead of an
  approval card; denying a pause is Stop. Only the local owner on a direct
  loopback connection controls it; other devices see the trace.
- A server-started step (a goal's next turn, work continuing in a design or
  folder the reply created) is a quiet centred note ("Goal · turn 2 of 10",
  "Continuing in Tiny date app"), never the person's bubble; Retry and Send
  again ignore it. Stop keeps the reply streamed so far, marked Stopped.
- Each generated result renders once, inline with its turn; Context outputs link
  to it. Embeds use `.rich-block` cards with a header toolbar of 28px icon
  actions; charts take their colours from `--chart-series-*`.
- The composer is one field (`.composer-field`, radius `--radius-composer`)
  that grows from a single 24px line to 240px. Chips appear inside it only when
  present. Left: `+` menu, model pill, approval shield; right: context ring,
  dictation with a Talk chevron, send/stop (34px round). The model pill reads
  the conversation's model status: a local (drive) or cloud glyph and the
  name, "Chat only" when tools are off, "Choose a model" when none is chosen,
  and for a model that can't answer a warning glyph (never "Ready") whose
  hint and the picker's first line say why ("Unavailable — Ollama isn't
  running") with Reconnect when that helps and Choose another model. The
  composer's one fix button follows the same status (Choose a model,
  Reconnect, Choose another model, or Set up a model when nothing can be
  chosen). Files attach by picking (several at once in a browser), dropping
  on the composer ("Drop to attach · up to 25 MB each") or pasting (a pasted
  screenshot gets a readable name); the limits (25 MB a file, 32 files, 100 MB
  a message) are said before anything is refused, naming each file left out.
  An image attached where the model can't see images says so with
  "Choose a vision model" (Vision defaults to "Same as chat model"). The
  picker is the shared `ModelList`: search, provider groups with a status dot
  and a billing tag (Subscription, Pay per use, Credits, Local · free), recent
  choices first; the list is re-read when the picker opens (at most every
  30 s). The context ring is
  neutral ink until it nears the compaction threshold. `/` opens commands and
  `@` opens agent profiles (the ones a person picks; the internal helpers
  stay out), write targets and files. Commands with an argument run in
  place: `/reasoning high`, `/profile writer`, `/goal <objective>` (and
  pause, resume, stop, done), `/agent [profile] <task>`; palette rows show the
  usage ("/goal objective") apart from the label. A welcome prompt fills the
  composer to edit and send. The default skill every chat starts with stays in
  Skills rather than a chip. Talk beside Dictate has its own glyph, Realtime
  Talk says it runs on OpenAI and is paid per minute, and Read aloud stays on
  replies without an on-device voice and says what is missing. Status lines are
  announced, not printed, except a failed or conflicting draft. Send and
  Stop are one button that morphs between the two. A composer narrower than
  480px is one line with the model, approvals and context usage under `+`. Floating
  composer menus (slash commands, model picker, Skills) open above the field
  and stay inside the viewport.

### Settings

Settings › Tools › Custom tools lists every custom tool (On, On · in chat,
Off) and unfinished drafts; a row opens to its commands with Test. A test
command that needs approval shows the approval card with the exact command
and runs once only after Approve; the code panel's builder uses the same step.
Add from a folder is a desktop folder pick that says first that it sends
excerpts to the chosen model; Remove confirms and keeps the files.

`features/settings/model.ts` is the one navigation/search/label/deep-link map:
seven groups ordered by how often people visit them — General (Preferences,
Appearance, Buddy) · Models (Providers, Models, Voice) · Knowledge (Memory,
Documents, Tracker) · Capabilities (Tools with the built-in tools, Skills,
Plugins, MCP) · Connections (Accounts, Channels) · Agents (Agent profiles) ·
System (System, Access, Updates, Data). Leaf ids are stable deep links.
Legacy ids and moved pages redirect to their new page and, when useful, to one
row (`/settings/utilities` → `/settings/tools#built-in-tools`,
`migration` → `data#migration`, `wiki` → `knowledge#wiki-vault`). Goals belong
to one conversation, so they live in its Context card; `/settings/goals`
opens the conversation. Unknown setting links return the index. Domain
settings remain typed capability forms; this metadata is not a form schema or
another backend settings store.

The shell is one canvas: a 232px navigation column (the `Settings` h1 and
Close, a `/`-focused search, then every group as a small label over its page
links) beside one scrolling page column capped at 840px. Below 900px of window,
or 640px of settings area (sidebar open, 200% zoom; a container query), a native
grouped picker (`Settings section`) replaces the column, and below 560px rows
stack their control under the label. Search lists matching
pages and individual settings (`settingsRows`); choosing a setting navigates
to `…#anchor`, and the shell opens any collapsed section or tab around the
element with that `data-setting-anchor`, scrolls it into view and highlights it
briefly (an outline instead under reduced motion). The ⌘K palette offers the
same pages and rows and still finds former page names.

Every page follows one anatomy (`features/settings/anatomy.tsx`):

1. **Header** — icon tile, title (`h2`, focused on navigation without a ring
   for pointer users), one line, and the page's summary chips plus an optional
   ↻, which the page portals in with `SettingsSummary`/`SettingsRefresh`.
2. **Essentials** — flat `SettingsSection`s of rows. Saved fields render as
   rows (`Field layout="row"`: label and help on the left, the control on the
   right; the help describes the control rather than naming it). Switches carry
   no On/Off text. Every change saves at once (decision 19): switches, selects
   and choices when changed, text when the field is left or on Enter (Escape
   puts the saved text back without sending anything). The save is one
   reviewed step (review and execute together) and the row then reads
   "Saved · Undo"; Undo sends the previous value. There is no Save or Revert.
   A change that sends data somewhere new asks first in place (choosing cloud
   embeddings: "Documents and memories will be sent to the cloud embedding
   provider…"). Typed credentials (API keys, channel tokens, secret fields)
   and entity editors (a custom endpoint, an MCP server, a plugin's
   configuration, a skill, a knowledge entry) keep an explicit Save. A row's
   Reset sits beside its control.
3. **Lists** — entity rows: icon tile, name with a `StatusDot` in words, one
   meta line, one primary action and a named ⋯ menu (`More actions for …`) for
   rarer verbs. Lists search inline as you type (Enter searches at once)
   instead of per-verb Search/Reload buttons; a reload that is meaningful sits
   in ⋯ or the header. Pages with a local library and a public catalogue use
   kept-mounted `SettingsTabs` (Installed | Discover).
4. **Advanced** — `SettingsAdvanced`, collapsed, for diagnostics, maintenance
   and rarely changed options. Forms inside a page (profile editor, MCP
   server settings) are one raised surface with the settings inputs, never a
   browser fieldset.
5. **Danger zone** — `SettingsDangerZone`, collapsed and outlined in the danger
   tone, never a filled button in the main flow; each `DangerAction` states
   what is lost and still asks for confirmation. A flow that starts from a row
   (e.g. removing a document) opens its Danger zone. Lists re-read after a
   confirmed change (document queue, MCP servers, skills), so a row never needs
   a manual refresh to show what just happened.

Removals that are easy to regret say so with Undo in the notice for 12 s
(`notify(message, tone, { label: 'Undo', run })`): removing a resource from a
conversation ("Removed X from this conversation." · Undo adds it back) and
dismissing an insight (Undo restores it).

**Settings › Data** (decision 21) has three parts. *Back up and restore*
(local owner on this computer only; other devices read that backups belong
to this computer): Back up now writes one zip to the workspace's Backups
folder in the background — conversations, memories, workflows, designs and
settings, read through SQLite's backup API — and never keys, sign-ins,
browser or channel sessions, caches, logs or runtime files; webhook secrets
and MCP headers/env are blanked, and the archive's manifest lists what to set
up again. The page shows the last backup time and Show in folder. Restore
from backup is desktop-only: one picked .zip (a one-use grant, never a path)
is checked (foreign, newer, unsafe or secret-bearing archives are refused),
described with what to sign in to again, and staged only after "Restore on
restart" confirms that exact review; a staged restore can be cancelled. On
the next start, before anything opens the profile, the server moves the
current profile aside into `before-restore-<time>`, moves the backup in and
rolls back on any failure, and the page then lists what to sign in to again.
*Import from another assistant* finds Hermes Agent or OpenClaw in its usual
folder (`~/.hermes`, `~/.openclaw` and the older names; shown home-relative,
never as a full path), or Browse picks the folder in the desktop app (a
one-use grant; a rescan with other choices names the preview instead); the
preview has Select all and Clear all over the items that can be imported.
*Danger zone* points to removing documents and keeps deleting all tracker data.

**Settings › Devices & remote access** (`/settings/access`) is one guided
flow, one list and Advanced. *Connect a phone or computer* (opened by its
button, so nothing is probed or created before) asks how the device reaches
this computer, from what is detected: Tailscale (private, HTTPS; checks this
computer's Tailscale once, shares after one confirmation, links Tailscale's
consent page when it asks and gives next steps for another Serve setting),
Same Wi-Fi (two lines of steps; "Allow on my network…" asks and restarts)
or Internet (the saved public link; starting it asks first). A loopback
address is never offered. Then one QR code with Copy link: every code is a
one-time, 10-minute invitation that renews 30 s before it expires (the old
one is cancelled; a few renewals at most) and is cancelled when the flow
closes; the link is never written on the page. It waits for the device, then
shows "Connected: <name> · Rename" and how to install Row-Bot on that
phone (over plain HTTP it says why it can't). *Your devices* lists each
signed-in device with "This device" (marked by the server), when and where
it was last seen in words, Sign out (confirmed; ends every session of that
device) and Rename. *Advanced*: where Row-Bot listens, allowed addresses, a
Tailscale share Row-Bot made (Copy, Stop sharing), the tunnel provider and
token, and the "Public" line with the address, Copy and Stop. A device
signed in by invitation renews its 30-day session by itself (at start and
every 12 hours); a signed-out or expired device is told how to connect
again.

Values speak human: enums are translated (`humanizeToken`: "Router",
"Private · on device", "HTTP", "Local process"), times are relative with the
full date on hover, and secrets read "Key saved · ····c99 · in keychain ·
Replace" (`maskedTail`, `credentialSourceLabel`) — never the value. The
settings snapshot reports each saved field's default; a field that differs
shows a small accent mark and a "Reset … to default" action. The default
model is the composer's searchable `ModelList` behind one trigger (name,
provider and billing tag); Providers show each provider's billing tag. API key
dialogs have a "Get a key" link, a format hint that warns before saving, a
note that saving sends the key to the provider, and the provider checks the
key before it is saved (a refused key is explained in the dialog and never
saved). Subscription sign-ins show their state in words, the device code
large with Copy and two steps, and are checked every 5 s by themselves.
Voice offers "Install Whisper <size>" with its download size. Pages read their
data when they open (GET only); anything that touches the network or another
system (catalog refresh, marketplace refresh, public skill search) stays an
explicit action. Controls keep 44px targets on coarse pointers.

### Home

Home is five tabs under one `Home capabilities` tablist, addressed by
`?tab=`: **Overview** (the default) · Workflows · Knowledge · Monitor ·
Insights. `?tab=workflows&workflow=<id>` opens one workflow's runs and then
drops only the `workflow` parameter. Home shares its reads: the knowledge graph,
the monitor snapshot and the first workflow page are reused for 20 s across
tabs, and nothing is read before the workspace is connected.

- **Overview** answers "what needs me?" in five sections: *Needs you*
  (setup to finish, conversations waiting on an approval, workflows waiting on
  one, failed workflow runs), *Running now* (live conversations and delegated
  agents), *Recent threads*, *Upcoming* (the next scheduled runs, in words) and
  *Since yesterday evening* (runs and maintenance since 18:00 yesterday). Rows
  open their conversation or workflow. Live rows are re-read when Overview
  opens and every 15 s while it stays open, so an approval that is resolved
  elsewhere clears here too. A conversation paused on its own approval reports
  `attention`/`waiting_approval` in the list, including after a restart.
- **Workflows** has a one-line header: title, inline search (as you type after
  350 ms; Enter at once), All · Enabled · Scheduled · Failed, a count, ↻, the
  delivery defaults behind a paper-plane icon, ⋯ and a small New workflow.
  Scheduled and Failed filter on the client over at most 20 pages and say
  "N matching in the first M" when more remained unread. Rows are
  Linear-style: glyph, name and a muted description, one meta line
  ("2 steps · Ran 3 minutes ago · Every Monday, Wednesday, Friday at 7:30"),
  the next run, a last-10 run sparkline, an enable switch, and Run, Edit and a
  named ⋯ (Run history, Edit workflow steps, Workflow settings, Open
  conversation, Delete workflow) that appear on hover or focus. A running row
  replaces the sparkline with "Step 2/4", which opens the run drawer. Run opens
  the same drawer (Run now, progress, stop, history) rather than starting at
  once. The editor is a builder: a step list with drag handles (keyboard: the
  handle with ↑/↓) and a right rail with the schedule builder (Manual · Once ·
  Repeats; days, time, every N hours/minutes or a cron expression) and its
  plain-language preview with the next run, delivery ("Send results to"), and
  model and approvals. The graph editor is a toggle in the same frame. The
  scheduler counts cron weekday numbers from Monday = 0, so the builder writes
  day names and describes numbered days as they actually fire. Every `task.*`
  command goes to `/tasks/commands`.
  Phase 13: ▶ on a row shows one review line in place ("3 steps · Row-Bot
  default · Blocks actions") with Run and Cancel; Run starts the run and opens
  the live run drawer, which re-reads the followed run every 2 s and ends with
  "Run finished · <status>". *Send results to* is "In this app" (always, not
  a choice) plus a checklist of the configured channels prefilled from the
  delivery defaults (`null` follows the defaults, `[]` is this app only; "Use
  my defaults" goes back). Settings has an agent profile select and a model
  combobox ("Default model" first). Typing `{{` in a prompt or step suggests
  the date variables and earlier steps' results (`step.<id>.output`). A
  webhook trigger shows its address with the secret masked and Copy (the real
  address is read only when copied), "Not reachable from the internet", and
  "Make reachable from the internet", which asks first because it opens the
  whole app through the tunnel; the webhook secret is compared in constant
  time and an empty one is refused. Duplicate workflow copies it without a
  schedule or trigger. Leaving the editor with unsaved changes asks "Save your
  changes first?"; deleting a workflow forgets its drafts; a one-off time that
  has passed is refused ("That time has passed…").
- **Knowledge** is a full-bleed sigma.js/graphology canvas (loaded as its own
  `graph` chunk; without WebGL the List view is shown with a note). Add memory
  (the toolbar's + and the empty map; also Settings › Knowledge) opens the
  knowledge editor blank. Nodes are
  circles sized by their number of links and coloured by type from a fixed,
  nine-hue palette plus a neutral Other (`--graph-1`…`--graph-9`,
  `--graph-other`), validated per theme for colour-vision-deficiency separation
  and the normal-vision floor on the canvas (#F3F5F7 light, #0B0E13 dark); a
  type keeps its colour when filters change. WebGL blends
  premultiplied colours, so canvas colours go through `glAlpha`. Labels thin
  out by zoom; edges are hairlines. Glass controls float over the canvas: a
  typeahead search (choosing a memory focuses it, highlights its neighbours,
  dims the rest and eases the camera; instant under reduced motion), Graph |
  List, filters (user hub, orphans, source), Dream Cycle and ↻; the type legend
  filters; zoom and fit sit bottom-right. Stats are one caption ("658 memories ·
  1,016 links · showing 250 · Show all"). When only part of the graph is loaded
  and the search finds nothing, it offers "Search all N memories". List is a
  dense, sortable, virtualized table. The inspector is a drawer inside the
  stage: summary, facts, tags, the source conversation and connections grouped
  by relation, with Edit, Merge or replace, and Delete (reviewed, then
  confirmed).
- **Monitor** opens with a health strip of tiles grouped from the diagnosis
  checks (Model runtime, Channels, MCP and tools, Scheduler, Knowledge,
  System); a tile opens a detail drawer with Run diagnosis. Below: a 24 h /
  7 d swimlane (Extraction, Dream Cycle, Workflow runs, Channel events),
  maintenance metrics with deltas and sparklines, Dream Cycle and extraction
  history tables, and a log console (12 px mono, a level stripe, level chips,
  search, follow new lines, collapse repeats, copy visible lines). The console
  keeps the full log it read when the snapshot refreshes.
- **Insights** is a feed: severity icon, one-line title with its category, a
  two-line summary, an expandable Why, one suggested action, and pin and
  dismiss icons; All | Pinned filters it. The skill library report renders as
  rows, never raw JSON.

### Design and Developer panels

A conversation shows a Design panel only when it has a design, and a Developer
inspector only when its code folder is bound. Both are dense tools in the side
panel; their layout follows the panel's own width through container queries
(`container: design` and `container: dev` in `ui/styles/panels.css`), not the
viewport. The panel's maximize control is a focus mode (the panel fills the
workspace; Escape or Exit focus mode returns). Any action that changes nothing
irreversibly is one reviewed step; destructive ones keep their confirmation.

- **Design** fills the panel with the page on a dotted ground, fitted by
  default (tall pages start at the panel width). A design that was just made
  (Add resource, a turn that made it, a duplicate) opens with the side region
  at its widest; one already open keeps the width the person chose. Add
  resource with a brief creates and drafts in one step ("Draft it now", on
  when a brief is given); while a turn works on the design the panel says so
  ("Drafting · Adding pages…", a live dot that stays still under reduced
  motion) and refreshes the page after each finished step. The top bar holds the
  design name (edit in place; Enter saves, Escape cancels), Preview | Edit,
  and icon actions: undo and redo (Mod+Z / Shift+Mod+Z, also from the
  canvas), history, properties, present, share, export, an info popover with
  the capabilities and review requirements, and ⋯ (search tools, pages and
  assets; insert blocks; review; import a document). A floating dock carries
  ◀ page N/M ▶ (the number opens a page menu, which also adds a page after the
  one shown and deletes the one shown), zoom (Fit, Width, 50–200%), a Size menu
  for page-based designs (16:9, 4:3, 1:1, A4, 9:16 · Phone; every page is
  re-fitted) and, for landing pages and app mockups, device width. A deletion
  or size change says what happened with Undo in place. ⋯ also offers
  Duplicate design (a "(copy)" bound beside it, opened in its own panel), and
  while a design is shown ⌘K lists its actions (Present, Export, Share or
  publish, Add a slide, Duplicate, Review, versions). From 720px the page
  strip sits on the left with thumbnails in the design's own aspect ratio and
  the inspector beside the canvas; below that the strip hides and the
  inspector is a sheet docked under the canvas, which refits above it. Edit
  opens the inspector only beside the canvas, so a narrow panel keeps the
  page clear to select on. A canvas that scrolls (Width or a zoom) takes
  keyboard focus; in a short bottom dock the canvas keeps 240px and the
  panel scrolls.
- In Edit, a click outlines an element with a label ("Heading · Launch day")
  and anchors an "Ask Row-Bot to change this…" field below it (above or
  inside when there is no room). Sending composes one message that names the
  page and the element and posts it through the open conversation's composer;
  the selection clears when the preview refreshes. Double-click edits text in
  place. The inspector has Properties (selection, the page's title and notes,
  its text, brand colours and logo, type) plus Library, Review and History
  tabs. Fields save on their own (text on blur or Enter, colours after a
  pause); the preview refreshes by itself and a manual refresh appears only
  on an error card. Undo restores the newest history snapshot; redo restores
  the state that undo replaced. Presenting takes the keyboard (arrows,
  Escape) and hands focus back to Present when it ends; Present fills the
  screen (Escape leaves full screen and ends it; notes stay off the audience's
  screen). Export is four format buttons (PDF, PNG, PowerPoint, HTML; pages and
  PowerPoint style under Options): on this computer one click exports and saves
  a copy into the workspace's Exports folder, then offers Open and Show in
  folder (local owner on direct loopback only); other devices get a Download
  button. Exports wait until the panel shows the saved version. Publishing
  asks once ("Publish these N pages at a local link? It opens on this computer
  only."); the Share sheet then shows the saved link with Copy link, Open, a QR
  code for remote links (drawn locally) and Unpublish. Sending to a channel or
  X always asks first. Brand takes colours and fonts "From a website" (one
  guarded read of a public page, applied through the brand control so Undo
  works). Review checks the design when it opens and after every change: safe
  findings have Fix, others Ask Row-Bot, and "Fix all safe issues" applies
  them as one step. The Edit-mode bridge is a static script the
  client's page policy allows by digest; its identity is a JSON data block.
- **Developer** opens with a status strip: folder, a branch chip (opens
  Git), ahead/behind, "N changed" or Clean, one checks dot, where commands
  run, refresh and an info popover with the safety boundaries (approvals,
  sandbox, network; clone, install, network and delete are never offered
  here). Four tabs follow: **Changes** groups files under the agent change
  that made them (newest first, earlier ones folded), then other changes; a
  unified or split diff folds unchanged lines and colours syntax. Undo is
  offered only for sandbox imports, whose originals the panel keeps; other
  agent changes are handed to the chat to revert. **Files** is a filterable
  tree with a highlighted preview. **Run** lists detected checks with ▶ and
  every process in the folder; Run reviews and starts in one step and starts
  only with the server's approval evidence, and the selected process streams
  into one console (follow, latest 2,000 lines). Commands run without a
  shell, so the Run tab explains `&&`, `|`, `>` and `<` before sending instead
  of calling it an approval failure; with more than one process running,
  Stop all stops them. The inspector opens on Changes when Git or the agent
  changed something. **Git** has a branch
  switcher, a commit box with a suggested message and file picks, and a pull
  request form with suggested text; push and pull requests confirm first.
  Without the GitHub command-line tool (or signed out) the pull request
  section shows the Connect GitHub card instead of a failure. The line
  terminal (desktop app) has Stop (Ctrl+C), Clear and "Open in your
  terminal", which opens the person's own terminal app at the conversation's
  code folder with Row-Bot's keys removed from its environment. Add resource
  names a new draft folder from its Name field ("Code folder" otherwise),
  says in a browser that picking a folder needs the desktop app, and Open
  saved can remove a folder from the list (files stay; Undo).
  Worktree, sandbox image and network sit under Advanced. Errors are neutral
  cards with the cause and one Retry.

### Responsive (phone and tablet)

Width classes come from the panel model: desktop from 1024px, tablet
768–1023px, phone below 768px (checked at 1440, 1280, 820, 390 and 360).
Phones and tablets are touch layouts: controls are 44px, hover-only actions
are always shown.

- **One header.** In a conversation the conversation header is the only top
  bar (48px). Phones show the navigation button (a back chevron to the
  conversation list), the title and a `Conversation menu` (⋯) holding
  Workspace commands (Mod+K), one entry per panel the thread has
  ("Open …"), Find in conversation, Context, Share or export and Rename.
  Tablets keep the icon actions in the same row (Find, Share, Workspace
  commands, Open panel, Context). Home and routed views keep one compact bar
  (navigation, title, search); Settings puts the navigation toggle and
  Workspace commands in its own header instead.
- **Drawer.** The navigation drawer is full width on phones and 280px on
  tablets, slides in from the left and closes from its header. It hosts the
  same sidebar: Pinned / Today / Yesterday / This week / Older and the type
  filter (All · Chats · Designs · Code · Workflows).
- **Panels as sheets.** Below 1024px a Design, Developer, terminal or
  browser panel opens as a full-height sheet over the conversation with its
  own header (Back to conversation, the panel kind, Close panel); the
  conversation and its draft stay mounted underneath, and the panel rail
  keeps other open panels one tap away. Tablets use the same sheet rather
  than a narrow dock: at 820px a dock would leave the chat at 400px and the
  panel at 420px, below the 720px the Design page strip and inspector need.
- **Context** is a full-height bottom sheet on phones and a 400px side sheet
  on tablets.
- **One-line composer.** A composer narrower than 480px (phones, or a chat
  squeezed by panels) is a single line: `+`, the field, dictation with its
  Talk chevron, and Send. The model, approvals and context usage move into
  `+` (the model picker then opens above the field); chips (agents, write
  targets, attachments, Set up a model) scroll on one row above.
- **Touch.** On coarse pointers (phones and touch tablets alike) controls
  keep 44px targets: every composer action and chip, the context ring,
  switch inputs (grown invisibly around their track), Settings buttons,
  disclosures, checkbox rows and fields; inline help links extend their hit
  area without moving the text, and a user message's actions sit under the
  bubble rather than beside it. The one-line composer's hint shortens to
  "Message" so it stays on one line at 360px.
- **Dialogs.** Tablets keep ordinary dialogs and the palette as centred
  cards; phones use full-screen tasks. Settings below 900px replaces the
  side navigation with a section picker but keeps its search above it.

### First run and Setup Center

Nothing is preset: a fresh profile has no chat, vision, image or video model
until the person chooses one, and removing a provider never falls back to
another model. Profiles that finished setup before this rule keep what they
were running on, written once as their own choices.

**First run.** Until a default model exists, opening Row-Bot at its plain
address opens `/setup` with one question, "How should Row-Bot think?", and
three choice cards (`aria-pressed`), stacked on phones:

- **On this computer** — Ollama, detected every 3 s over loopback while Setup
  is open (no refresh button): not installed (the steps for this OS and a
  Download Ollama link), installed but not running, or running with its
  models listed. Only a known "can't use tools" is tagged Chat only.
- **With my subscription** — ChatGPT, Claude or Grok (a `Segmented`), signed
  in inside Setup with the same compact account dialog Settings uses.
- **With an API key** — OpenAI, Anthropic, Google Gemini and OpenRouter
  first, the rest under More providers, each with its billing tag; the key
  dialog is the one Settings uses.
- *Other (custom endpoint)* is a link to Providers with the add dialog open;
  models of a connected endpoint are listed back in Setup.

Choosing a model saves it as the default and pins it for the composer; a
one-message test runs ("Checking the model"); then Home opens. A failed test
keeps the person in Setup with the reason, Try again and Choose another
model. The only extra step is an offer to import another assistant's data
when one is found. The first run never traps anyone: deep links and Home tabs
are never redirected, "Set up later" opens Home for this window (its card
then reads "Choose how Row-Bot thinks"), and a profile with a saved default
never sees the first run.

**Setup Center.** Once a model exists, `/setup` (also in the palette and Home
› Overview) has an icon header with a progress ring that counts done and
skipped apart ("4 of 11 done · 2 skipped"). "What would you like to use?" is
a grid of selectable tiles (native checkboxes; each change saves at once).
Every area is a tile with an icon, a one-line description, a status chip
(Done, Skipped, Recommended, To do) and one primary action; Mark done and
Skip sit in its ⋯. Areas read their real state: a chosen model and Developer
tools (on unless switched off) are done and can't be skipped. Areas that
match the chosen uses are recommended and come first. Every choice is one
reviewed, idempotent command; an uncertain one is kept for "Check setup
action".

### Desktop Buddy (overlay)

The torn-off Buddy is its own small entry, `frontend/buddy-overlay.html` →
`src/overlay/`, served at `/app-v2/buddy-overlay` in a 380×230 frameless,
always-on-top window. A separate entry beat a lazy route in the main bundle
(Edge at 380×230, warm: ready 744 vs 820 ms median, 282 vs 564 KB script,
4 vs 59 KB CSS). It loads only fonts, tokens and its own stylesheet, and the
asset manifest and `build:verify` require it.

- **Header (drag region).** 40px avatar with its activity ring, the
  conversation title (h1) and "kind · state" (Chat · Responding…). Three 28px
  icon actions: Open full thread, Dock Buddy, Hide Buddy. Dragging the
  header moves the window through pywebview's own move channel.
- **Body.** The latest answer of the followed conversation as plain text,
  scrollable and focusable, with a caret while it streams; before words
  arrive, the last three steps ("Searching · flights to Lisbon"). A
  stopped or tool-only turn never shows an older answer. Notices: an
  interrupted turn offers Resume, a failed one Open thread, a message with
  no reply says so.
- **Approval.** One row: "Allow X?" with the key argument, Deny, Details
  (opens the full thread) and Approve (Mod+Enter).
- **Composer.** "Message this thread", Enter sends, Shift+Enter breaks the
  line, Send morphs to Stop. While a turn runs or waits, Enter says why it
  did not send and keeps the text. Drafts are the conversation's own and
  follow the main window both ways.
- **Following.** Buddy shows the conversation selected in the main window
  (read through its bridge on a change hint, on focus and every 5 s), else
  the most recent one. Open full thread brings the main window forward at
  that conversation.
- **Look.** Tokens and appearance follow the app setting (System by
  default). Windows keeps an opaque rectangle; macOS is transparent with
  rounded corners. Reduced motion removes the ring, caret and shimmer.
  CSS only, and nothing animates out. The window is revealed only after the
  first settled render and the avatar (or 700 ms).

The native wiring (window roles, attested bridge, lease renewal, grant
re-attestation) is in `docs/ARCHITECTURE.md` › Buddy Desktop Overlay. What the
default app still loads from NiceGUI, as the checklist for removing it, is in
`docs/NICEGUI_RETIREMENT.md`.

## Errors, notices and recovery

People see what happened and one way forward; codes, receipts and retries
stay underneath.

- **Errors.** Every problem code the server can return maps to one sentence
  and at most one fix: Retry, Reconnect, Choose a model, Open *the exact
  setting*, or Send now (`frontend/src/api/errors.ts`). A unit test walks
  `contracts/client-platform/v1/error-codes.json` and fails on any code
  without a sentence, on a setting link that does not exist, and on internal
  words. An unknown code reads "Something went wrong. Try again." with the
  code under Details. Visible copy never says receipt, replayed, admission or
  owned. Command receipts still stop a retried request from running twice: a
  request refused before it ran drops its claim so the next try is fresh; one
  that may have run offers "Check message" rather than sending again.
- **Waiting messages.** A message sent while Row-Bot is working waits on the
  server. Above the composer: "1 message waiting · sends when Row-Bot
  finishes", each with Send now, Edit and Discard (Discard asks first). The
  list is read from the server (`queue?waiting=1`), not counted from events,
  so it matches what will send. Stop pauses waiting messages before it
  announces the stop, so Send now works straight after; a stopped reply offers
  Send again only when nothing is waiting. Send again and Retry resend the
  message's files, never duplicate it, and never leave a claim that blocks the
  next message.
- **Attachments.** A sent file shows as a chip on the person's message; the
  context Row-Bot extracts from it stays out of the bubble.
- **Background notices.** `notify()` (workflow results, reminders, approvals
  waiting, document batches, account health) and start-up warnings (a plugin
  that failed to load, a tunnel that did not start, an expiring token) land in
  one bounded server journal: the latest 64 notices and 32 start-up warnings,
  with a repeat within ten minutes folded into one notice with a count. A
  client that asks (`notices_epoch`) gets them on the conversation event
  stream (`event: notice`, or `notices` on the poll page); with no
  conversation open it reads `GET /notices` every 30 s. Warnings and errors
  always show; information shows only for jobs the person started. Each shows
  once per device for each start of Row-Bot (a window that shows it records
  it; another window opened later does not repeat it), and start-up warnings
  stay listed in Monitor › Start-up.
  Desktop (OS) notifications are unchanged.
- **Recovery after a restart.** When the server restarts, the window
  re-handshakes by itself (after 0.5, 1.5, 3 and 5 s), keeps the open
  conversation and any unsent drafts, and resubscribes. If that does not
  work, the banner offers Reconnect (Reload only when this page itself must
  update); Settings shows the same retry state. Connection problems never send
  people to the previous app.

### Tunnels and shutdown

A public tunnel is exposure, so it never outlives Row-Bot.

- Every exit closes the tunnels this process opened: a normal quit, a
  shutdown while work is still stopping, and interpreter exit. The ngrok
  agent is recorded as Row-Bot's own in the data folder (`runtime/`) and, on
  Windows, ends with the server process. The launcher's forced stop cleans up
  the agent of the server it stopped, and the next start stops agents left by
  a crash. Only recorded agents whose Row-Bot process is gone are stopped; an
  ngrok agent Row-Bot did not start is never touched. Closing the last tunnel
  also ends the idle agent, which would otherwise hold one of the account's
  sessions.
- Failures are words, not ngrok codes: a session-limit refusal says the
  account already runs as many agents as it allows and where to stop one.
- Settings › Devices & remote access › Advanced shows the tunnel as it is in
  one "Public" line: running (with how many public
  addresses), not running with the reason, set up and not running, or not set
  up. Monitor agrees, and a running SMS channel without a public address is a
  warning, not "Running". "Check tunnel setup" reports what it found. A tunnel
  that fails at start-up is a start-up warning. Starting SMS opens the shared
  tunnel by design.

### Desktop window

The desktop window's web view has no menu of its own, so right-click opens
Row-Bot's: Cut, Copy, Paste and Select All in a field that can change; Copy
and Select All on read-only text and messages; never Copy or Cut on a
password. Commands act on the selection the right-click found, and Paste is an
ordinary edit, so the composer and forms see the change. Browsers keep their
own menu.

## Motion

Motion confirms a state change; it never delays one. Tokens: 120ms popovers,
180ms panels and sheets, 280ms drawn glyphs (`--motion-draw`), `--ease-out`.
Under reduced motion every token is 0ms and animations are removed, and the
resting style is always the finished state, so nothing is lost.

- **Send ↔ Stop** is one button: the arrow lifts away as the square settles
  in and the fill turns from accent to ink, so its place and keyboard focus
  hold while a response starts and ends.
- **A finished turn draws its check.** When this client watched a turn's
  tools finish, the activity row's check strokes itself in and each finished
  step's glyph settles; history loaded later never animates.
- **Copy turns into a check** (`CopyGlyph`) on messages, code, tables, step
  results and Monitor copies, then returns after 1.6s.
- Sheets slide up, the drawer slides in from the left, popovers fade and
  lift by 2px. CSS only; no motion library.

## Accessibility, effects and visual regression

The `/app-v2/primitives` route exercises every public primitive and token family.
Use it with both appearances, all accents and the five supported viewport sizes.
Reduced motion removes animations; reduced transparency, unsupported filters,
phone layouts, forced colours and `data-low-performance="true"` force opaque
surfaces. The low-performance hook is a presentation input, never native or data
authority. Forced colours preserve system focus/borders. Do not use a screenshot
alone as proof of hit-testing, focus order, minimum size or contrast.

For a visual change, run unit/theme contracts, build the explicit fixture bundle,
and run the isolated browser suite in the testing guide. Inspect full screenshots,
actual control hit tests, console/network evidence, axe results and raw timing
samples at 1440×900, 1280×720, 820×1180, 390×844 and 360×800. Keep failures and
correct the component; do not broaden error allowlists or replace golden evidence
to make a failing check pass. Record source/asset hashes with each evidence cut.
Regenerate the normal production bundle before payload/reproducibility checks.

`tests/browser/polish-snapshots.spec.ts` holds pixel baselines only for fully
synthetic surfaces (the component gallery in both appearances, the in-browser
fixture conversation and the Setup Center on the fixture server) with the clock
frozen, bundled fonts and finished animations, for Chromium desktop and phone on
the platform that recorded them. Elsewhere they skip rather than compare
different font rasterisation; record new ones with `--update-snapshots`. Views
over real or time-relative data stay in the manual capture sets.

Browser emulation, CSS zoom, axe and fake native drivers do not certify physical
keyboards/safe areas, screen readers, actual browser chrome zoom or OS dialogs.
Record those manual limitations explicitly in the phase gate. The full transcript,
streaming token DOM, preview isolation and capability-specific UI arrive in their
scheduled phases; foundation measurements must retain that scope distinction.
