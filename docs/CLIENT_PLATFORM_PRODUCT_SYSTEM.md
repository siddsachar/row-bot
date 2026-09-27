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

## Public primitive inventory

| Surface | Contribution contract |
| --- | --- |
| `Button`, `Input`, `Select`, `Field` | Native semantics and labels; primary/secondary/ghost/danger actions; disabled states and named icon buttons. Select stays a native browser control. |
| `Tabs`, `Menu`, `Popup`, `Hint` | Radix owns keyboard, focus and dismissal behavior. Floating surfaces layer above their active task; give each trigger an accessible name. Menus are bounded by Radix's available width/height and scroll internally, revealing the current choice; `Hint` accepts an optional `shortcut`. Menu actions may carry a 16px monochrome `icon`, a keycap `shortcut` (announced through `aria-keyshortcuts`, kept out of the item's name) and `separatorBefore`; destructive actions always render last, in red, after a separator. Only one transient popover shows at a time: composer-owned popovers such as the slash palette step aside while focus is in another control. |
| `OverlayProvider`, `useOverlay` | One Radix modal scope with title/description; dialogs, short sheets, navigation drawers, the command palette (`kind: 'palette'`: no header or footer chrome, title kept for assistive tech) and alert-dialog semantics share it. |
| Notifications | `notify` coalesces duplicate text and retains at most three notices. Notices wait while a modal is open, so they cannot cover its footer or consume Escape; Radix pauses dismissal on focus/hover after display. Errors also need a persistent inline recovery action. |
| `Skeleton`, `EmptyState`, `ErrorState`, `Progress` | Name the operation; delay skeleton visuals 150ms with cancellation; never invent percentage progress. Empty states explain a useful next step. |
| `Surface` | Opaque by default. The optional elevated effect has a 94% overlay backing and bounded blur only with supporting CSS and appropriate preferences. |
| `IconButton`, `Kbd` | Icons for verbs: 28px (`sm`) or 32px (`md`) on fine pointers and 44px on touch. The `label` is required and is both the accessible name and the tooltip; an optional `shortcut` such as `Mod+K` renders keycaps (⌘ on macOS, Ctrl elsewhere) and sets `aria-keyshortcuts`. When a text button becomes an icon button, keep its accessible name. |
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

Desktop dialogs are bounded to 560px and viewport minus 48px. Compact forms use
a full task surface. Short sheets use at most 85dvh; navigation uses a 280px
tablet drawer and a full-screen phone list with Back. Overlay keys allow an owner
to dismiss its responsive surface without dismissing an unrelated task.
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
the list still holds one item per conversation. Rows show a monochrome type
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
Escape or an outside click closes it; compact layouts use a sheet. Design,
Workspace and Terminal panels keep the full-height right region, labelled
`Side panels` only while it holds panels. Exactly one `Close all panels` is
visible: in the side region, else the bottom region, else a floating rail that
lists panels whose region is hidden. Open panel lives in the conversation header
on desktop and in the compact controls below 1024px. Live delegated agents open
and promote Context's Agents section by CSS order, never by remounting it.

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

The conversation is airy; tools inside it stay dense. The transcript and the
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
- Each generated result renders once, inline with its turn; Context outputs link
  to it. Embeds use `.rich-block` cards with a header toolbar of 28px icon
  actions; charts take their colours from `--chart-series-*`.
- The composer is one field (`.composer-field`, radius `--radius-composer`)
  that grows from a single 24px line to 240px. Chips appear inside it only when
  present. Left: `+` menu, model pill, approval shield; right: context ring,
  dictation with a Talk chevron, send/stop (34px round). The context ring is
  neutral ink until it nears the compaction threshold. `/` opens commands and
  `@` opens agent profiles, write targets and files. Status lines are
  announced, not printed, except a failed or conflicting draft. Floating
  composer menus (slash commands, model picker, Skills) open above the field
  and stay inside the viewport.

### Settings

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
   no On/Off text. A switch saves when flipped; selects, choices and text
   fields keep an explicit Save and Revert. Either way the save is one
   reviewed step (review and execute together). A row's Reset, Save and
   Revert sit beside its control.
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

Values speak human: enums are translated (`humanizeToken`: "Router",
"Private · on device", "HTTP", "Local process"), times are relative with the
full date on hover, and secrets read "Key saved · ····c99 · in keychain ·
Replace" (`maskedTail`, `credentialSourceLabel`) — never the value. The
settings snapshot reports each saved field's default; a field that differs
shows a small accent mark and a "Reset … to default" action. Pages read their
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
- **Knowledge** is a full-bleed sigma.js/graphology canvas (loaded as its own
  `graph` chunk; without WebGL the List view is shown with a note). Nodes are
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
  default (tall pages start at the panel width). The top bar holds the
  design name (edit in place; Enter saves, Escape cancels), Preview | Edit,
  and icon actions: undo and redo (Mod+Z / Shift+Mod+Z, also from the
  canvas), history, properties, present, share, export, an info popover with
  the capabilities and review requirements, and ⋯ (search tools, pages and
  assets; insert blocks; review; import a document). A floating dock carries
  ◀ page N/M ▶ (the number opens a page menu), zoom (Fit, Width, 50–200%) and,
  for landing pages and app mockups, device width. From 720px the page
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
  Escape) and hands focus back to Present when it ends. Exports download
  locally and wait until the panel shows the saved version; sharing and
  publishing always ask first. The Edit-mode bridge is a static script the
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
  into one console (follow, latest 2,000 lines). **Git** has a branch
  switcher, a commit box with a suggested message and file picks, and a pull
  request form with suggested text; push and pull requests confirm first.
  Worktree, sandbox image and network sit under Advanced. Errors are neutral
  cards with the cause and one Retry.

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

Browser emulation, CSS zoom, axe and fake native drivers do not certify physical
keyboards/safe areas, screen readers, actual browser chrome zoom or OS dialogs.
Record those manual limitations explicitly in the phase gate. The full transcript,
streaming token DOM, preview isolation and capability-specific UI arrive in their
scheduled phases; foundation measurements must retain that scope distinction.
