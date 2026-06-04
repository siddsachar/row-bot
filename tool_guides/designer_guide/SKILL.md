---
name: designer_guide
display_name: Designer Guide
icon: "🎨"
description: Guidance for creating and editing designs using the designer tool.
tools:
  - designer
tags: []
---
DESIGNER TOOL:
- You have designer tools that create/edit multi-page visual designs.
- Each project has a **mode**, a **canvas aspect ratio**, multi-page content, and an optional brand config.
- All designs are rendered as HTML/CSS in a sandboxed iframe preview.

## Project modes (pick one — the mode dictates layout, canvas, and tool surface)

| mode | What it produces | page_kind | Default aspect | Canvas behaviour |
|---|---|---|---|---|
| `deck` | Slide deck (16:9 / 4:3 / 1:1 / 9:16) | slide | 16:9 (1920×1080) | **Fixed** body — `width:Wpx; height:Hpx; overflow:hidden`. Each page is a single slide. |
| `document` | A4 / Letter document pages | slide | A4 (794×1123) | **Fixed** page body — same fixed-frame rules as deck. |
| `landing` | Tall scrollable landing page | screen | landing (1440×3200 hint) | **Tall scrollable**. Use `html,body{min-height:100vh;overflow-x:hidden}` and a `.page{max-width:1440px;margin:0 auto}` wrapper. NEVER set `body{overflow:hidden}` or fixed pixel `height`. Use responsive units (`vw`, `clamp()`, `%`). |
| `app_mockup` | Phone or desktop UI prototype | screen | phone (390×844) | **One device viewport per page/route**. `html,body{width:Wpx;height:Hpx;overflow:hidden}`. In-content scrolling goes inside an inner `.screen-body{overflow-y:auto}`. |
| `storyboard` | Cinematic shot-by-shot board | shot | 16:9 (1920×1080) | Fixed-frame like deck. Each page = one shot with beat / caption / camera direction. |

The user picks mode in the New Design dialog. You can read it with `designer_get_project` and change it with `designer_set_mode`. Switching modes does NOT auto-resize the canvas — call `designer_resize_project` after `designer_set_mode` if you also want a different aspect.

## Core page operations
- `designer_set_pages`: Replace ALL pages. Use for new projects or full reworks. Input: list of `{html, title, notes}`.
- `designer_update_page`: Update a single page HTML. Input: `index`, `html`, optional `title`.
- `designer_add_page`: Insert a new page. Use `index=-1` to append.
- `designer_delete_page`: Remove a page by index. DESTRUCTIVE — requires user approval.
- `designer_move_page`: Reorder a page. Input: `from_index`, `to_index`.
- `designer_get_project`: Read project summary (mode, page titles, brand, dimensions). **Call before any multi-page edit.**
- `designer_get_page_html`: Read the full stored HTML for a single page. Call before full-page rewrites so you preserve existing assets and layout.
- `designer_get_reference`: Read a saved project reference by id or filename so you can reuse prior attachments without asking again.

## Interactive modes — `landing` and `app_mockup`
For these modes, **never write raw `<script>` or `onclick=`**. The preview is sandboxed and a thin runtime bridge wires interactivity from declarative attributes.

### Declarative attributes the runtime understands
- `data-row-bot-route="<route_id>"` → marks a screen/route section. The runtime shows one route at a time. The outer route container also gets `data-row-bot-route-host="1"` automatically when emitted via `designer_add_screen`.
- `data-row-bot-action="navigate:<route_id>"` → click handler that switches to the named route.
- `data-row-bot-action="toggle_state:<key>"` → click handler that flips a state key. The runtime sets `data-row-bot-state-<key>="on|off"` on `<html>`; style with `[data-row-bot-state-<key>="on"]` selectors.
- `data-row-bot-action="play_media:<asset_id>"` → click handler that plays a video/audio asset.
- `data-row-bot-transition="fade|slide_left|slide_up|none"` (optional, on the navigation source).

### Tools for interactive projects
- `designer_add_screen`: Add a new screen/route. Input: `title`, optional `route_id` (auto-slugified from title), optional `html` (branded blank if empty), optional `copy_from` (page index to duplicate).
- `designer_link_screens`: Wire a click on one element to navigate to another screen. Input: `source_route`, `selector` (CSS or `data-row-bot-element-id`), `target_route`, optional transition.
- `designer_set_interaction`: Generic — attach `navigate` / `toggle_state` / `play_media` to any selector. Input: `source_route`, `selector`, `action`, `target`, optional `event`, optional `transition`.
- `designer_reorder_routes`: Reorder the route list (which is the page list for screen-mode projects).
- `designer_preview_screen`: Render a single route in isolation for review.
- `designer_set_mode`: Switch the project's mode (and tool surface).

### Authoring rules per interactive mode
- **`landing`**: usually ONE page that scrolls vertically. Sections (`<section id="...">`) for hero / features / pricing / FAQ / CTA / footer. Use anchor links (`<a href="#features">`) for in-page jumps; reserve `data-row-bot-action="navigate:..."` for cross-route flows in multi-page landings.
- **`app_mockup`**: ONE route per logical screen (Home, Detail, Settings, etc.). Each page's HTML is the entire device viewport. Use `data-row-bot-action="navigate:<route_id>"` on tappable rows, tab bars, and back buttons. Use `data-row-bot-transition="slide_left"` when going deeper, `slide_right` when going back. Wrap scrollable content inside an inner `.screen-body{overflow-y:auto}`.

## Media (images, video)
- `designer_generate_image`: Generate an AI image from a text prompt and embed it in a page. Input: `prompt`, optional `page_index` (-1=active), `position` (top/bottom), `width`, `height`, `size`. When a page contains a `data-row-bot-shot-visual` or `data-row-bot-image-slot` placeholder (storyboards, typed deck slots), the generated image automatically replaces the placeholder and fills the slot — no extra positioning call needed for the first generation.
- `designer_insert_image`: Insert an attached or local image file. Same slot-replacement behavior as generate.
- `designer_generate_video`: Generate an MP4 from a text prompt (or image-to-video). Input: `prompt`, optional `page_index`, `position`, `width`, `aspect_ratio`. Embedded as `<video autoplay loop muted playsinline>` with a poster.
- `designer_insert_video`: Insert an attached / local video file (mp4/webm/mov). Same autoplay/loop/muted defaults.
- `designer_move_image`, `designer_replace_image`, `designer_move_element`, `designer_duplicate_element`, `designer_restyle_element`: targeted DOM-level edits.
- `designer_remove_image`: Remove an inserted image, chart, or video from a page WITHOUT deleting the page. Input: `image_ref` (asset ID or label), optional `page_index` (-1=active). Use this — NOT `designer_delete_page` and NOT `designer_replace_image` — when the user says "remove the picture", "delete the image from shot 2", "clear the visual", etc. Shot-visual / image-slot placeholders revert to their dashed preview automatically.
- When reusing a known project asset inside hand-written HTML, use `src="asset://ASSET_ID"` and keep `data-asset-id="ASSET_ID"` on the `<img>` / `<video>`. Never invent placeholder tokens like `__ASSET_...__` or `__REMOVE__`.

## AI content & polish
- `designer_refine_text`: Refine a text element. Input: `page_index`, `tag` (e.g. 'h1', 'p'), `old_text` (exact text), `action` (shorten/expand/professional/casual/persuasive/simplify/bullets/paragraph/custom), optional `custom_instruction`.
- `designer_add_chart`: Add a chart. Input: `chart_type` (bar/line/pie/scatter/donut/histogram/box/area/heatmap), `data_csv` (inline CSV with header), optional `title`, `page_index`, `position`.
- `designer_insert_component`: Insert a curated reusable block (hero callout, stats band, testimonial, pricing cards, timeline section).
- `designer_critique_page`: Review for hierarchy, overflow, contrast, readability, spacing.
- `designer_apply_repairs`: Apply safe deterministic fixes for selected critique categories.
- `designer_brand_lint`: Read-only scan for contrast issues, off-palette colors, non-brand fonts, missing alt text, logo safe-zone overlaps. Input: optional `page_index` (-1=all). Returns structured JSON.
- `designer_generate_notes`: Generate speaker notes for one page (deck/document modes).

## Brand & canvas
- `designer_set_brand`: Update brand colors/fonts and logo placement. Includes `logo_mode`, `logo_scope`, `logo_position`, `logo_max_height`, `logo_padding`.
- `designer_resize_project`: Resize the canvas using a built-in preset or explicit aspect ratio. Available aspects: `16:9`, `4:3`, `1:1`, `9:16`, `A4`, `letter`, `landing` (1440×3200), `phone` (390×844), `desktop` (1440×900). Use after `designer_set_mode` if changing both.

## Export & publish
- `designer_export`: Input: `format` (`pdf`/`html`/`png`/`pptx`), optional page range. PDF/PNG/PPTX work best for `deck` and `document`. Single-file `html` works for any mode.
- `designer_publish_link`: Publish a self-contained shareable HTML deck link. For `landing` and `app_mockup`, this produces an interactive bundle (includes the runtime bridge + transitions CSS) so navigation and toggles work in the published page.

## HTML rules (all modes)
- Each page MUST be a complete self-contained HTML document with inline `<style>`.
- Use CSS variables for brand: `--primary`, `--secondary`, `--accent`, `--bg`, `--text`, `--heading-font`, `--body-font`.
- Canvas size + mode are provided in the system prompt — follow the canvas rules table above.
- Include Google Fonts `<link>` if using custom fonts.
- Modern CSS only: flexbox, grid, gradients, box-shadow. No external frameworks.
- For placeholder images: colored SVG shapes or gradient divs, never external URLs.
- **No `<script>` and no inline `onclick`** — the iframe is sandboxed. Use `data-row-bot-action` for interactive modes; deck/document/storyboard pages have no interactivity.

## Content budgets per mode (anti-clipping)

Fixed-slide modes (`deck`, `document`, `storyboard`) clip with `overflow:hidden` — anything past the canvas height is lost. Stick to these per-page budgets:

- **`deck` (16:9 @ 1920×1080)**: ONE heading + EITHER (a) one paragraph ≤45 words, (b) up to 3 cards/columns with ≤25 words each, OR (c) one chart/image + ≤2 bullets. Max 5 bullets. 64–96 px edge padding. Heading ≤4.5rem, body ≤1.4rem, line-height 1.3–1.5. Leave ≥48 px of bottom breathing room.
- **`document` (A4 / Letter)**: 130–160 words body copy per page (lead paragraph + 3–4 short sections with ~2 bullets each). Max ~5 top-level sections per page — split into more pages rather than cramming. Generous line-height (1.45–1.6), 16–24 px gap between sections, ≥32–48 px bottom padding.
- **`storyboard` (1920×1080)**: each shot gets ONE eyebrow (≤3 words) + ONE heading (≤6 words) + ONE short paragraph (≤35 words) OR a pull-quote card + AT MOST two small metadata cards (shot type, camera move) OR one voiceover card — not both + ONE direction/note line (≤30 words) + ONE footer strip (timeline OR shot counter). Pick 3–4 visible blocks max. Push extra detail to page notes via `designer_generate_notes` instead of the visible frame. Right column uses `display:flex; flex-direction:column; gap:20–28px; padding:64px 56px;` with ≥48 px bottom space.

Landing and app_mockup modes flow differently — landing pages scroll vertically, app mockups fit one device viewport per route — but the same principle applies: count blocks before you write HTML, and split rather than cram.

## Authoring guardrails (all modes)

- **No decorative overlap on text.** Never place absolutely-positioned decorative CSS art (blobs, hand-drawn shapes, mascots, chef figures, etc.) on top of a heading or body paragraph. If a hero has a headline, either (a) give the illustration its own column/row, (b) put the illustration behind the text with low opacity and `z-index:0` AND ensure the text has a readable background or `text-shadow`, or (c) omit the illustration. When in doubt, use a real AI image via a typed image slot instead of CSS shapes.
- **Button rows stay horizontal.** When emitting two buttons side-by-side (Back + primary, Cancel + Confirm, etc.), use `display:flex; gap:12–24px;` and give the primary button `flex:1`. Never let them stack vertically or touch. Secondary / ghost buttons must be visually distinct (transparent or outlined) from the primary action — never two identical filled pills.
- **Typed image slots over overlays.** When authoring a page that will later receive an AI image, mark the target container with `data-row-bot-image-slot="NAME"` (e.g. `<div data-row-bot-image-slot="hero" style="width:100%;aspect-ratio:16/9;"></div>`). `designer_generate_image` / `designer_insert_image` will fill the first matching slot, sized to cover. Alternatively pass `position="replace:.my-class"` or `position="replace:#my-id"` to target a specific container. Never emit an absolute-positioned image overlay unless the user explicitly asked for a floating element.

## Post-critique repair loop (mandatory after major rewrites)

After any full-page rewrite, full-project rebuild, mode switch, canvas resize, or multi-page update:

1. Call `designer_critique_page(page_index=<n>)` on each changed page.
2. If any finding category is `overflow`, either:
   - call `designer_apply_repairs(page_index=<n>, categories=["overflow"])`, OR
   - trim content and call `designer_update_page` again, OR
   - split to a new page via `designer_add_page`.
3. Never ship a page with an unresolved `overflow` finding.

For broader sweeps, pass multiple categories: `designer_apply_repairs(categories=["spacing","readability","overflow"])`.

## Workflow patterns

### Decks / documents / storyboards (static)
- Creating: `designer_get_project` → `designer_set_pages` with all pages.
- Editing one slide: `designer_update_page` with the affected index.
- "Make all pages darker": `designer_get_project` → `designer_update_page` per page.
- "Add a pricing slide after slide 3": `designer_add_page(index=3, ...)`.
- "Export as PDF": `designer_export(format="pdf")`.
- Speaker notes: `designer_generate_notes` for the relevant page (don't rewrite the HTML).

### Landing pages
- One page is fine. Build vertically with `<section>` blocks: hero, features, pricing, FAQ, CTA, footer.
- Use `clamp()` and `vw` units for typography. Use `max-width:1440px` on a `.page` wrapper.
- For multi-page landing flows (e.g. a stepper), use `designer_add_screen` per step and `designer_link_screens` for the navigation.

### App mockups
- Plan routes first: list every screen the user mentions. Call `designer_set_pages` (or `designer_add_screen` per route) so each page has `data-row-bot-route="<id>"` on its outer container.
- Wire navigation explicitly with `designer_link_screens` or `designer_set_interaction(action="navigate", ...)`. Don't use `<a href="#detail">`.
- For toggleable UI (dark mode, notifications, expanded panels), use `data-row-bot-action="toggle_state:<key>"` and style with `[data-row-bot-state-<key>="on"]` selectors on `<html>`.
- Wrap scrollable content inside an inner `.screen-body` — the outer `body` stays at the device viewport.

### Mode change
- "Turn this deck into a landing page": `designer_set_mode("landing")` → `designer_resize_project("landing")` → `designer_set_pages([...])` rewriting the content as a single tall page.

### Canvas / brand
- Canvas changes: `designer_resize_project` before restyling when the user wants square, vertical, A4, Letter, phone, desktop, landing, or standard slide formats.
- Brand changes: `designer_set_brand` first. It updates stored brand CSS and can switch the automatic logo overlay between all pages, first page only, or manual placeholder mode.

### Sharing
- Shareable deck/landing URL: `designer_publish_link` (handles interactive bundles automatically when mode is `landing` or `app_mockup`).
- One-off file: `designer_export(format="html")` for a single self-contained file.

### AI imagery
- "Add a photo of mountains": `designer_generate_image(prompt="cinematic photo of mountain landscape at sunrise")` — or ask the user to attach a reference image and call `designer_insert_image`.
- "Generate an AI image of a futuristic city": `designer_generate_image(prompt="futuristic city skyline")`.
- "Add a 4-second product video": `designer_generate_video(prompt="...", aspect_ratio="16:9")`.
- "Generate a still image for every shot": call `designer_generate_image` once per page — shot-visual placeholders are filled automatically in the correct slot for each.
- "Remove the picture from shot 2" / "delete the image on this page": call `designer_remove_image(image_ref="<asset-id or label>", page_index=<n>)`. Do NOT use `designer_delete_page` (that removes the whole page) and do NOT pass a fake sentinel to `designer_replace_image`.

### Components & critiques
- "Add a metrics strip near the top": `designer_insert_component(component_name="stats_band", page_index=-1, position="top")`.
- "Review the current slide and fix what feels cramped": `designer_critique_page(page_index=-1)` → `designer_apply_repairs(page_index=-1, categories=["spacing","readability","overflow"])`.
- "Make the heading shorter": `designer_refine_text(page_index=0, tag="h1", old_text="...", action="shorten")`.
- "Add a bar chart of Q1 revenue": `designer_add_chart(chart_type="bar", data_csv="Quarter,Revenue\nQ1,120\nQ2,150\n...")`.
- "Show me what you changed" / "review last turn": the UI surfaces a review dialog with a page-by-page mutation diff after every agent turn — you don't need to call it, but do reference it when the user asks what you altered.

## IMPORTANT
- Refs from `designer_get_project` can go stale. Re-read if unsure.
- Always explain what you changed in your text response after tool calls.
- Maintain visual consistency across pages.
- Keep HTML compact — avoid unnecessary nesting or unused CSS.
- AI images: descriptive prompts. Specify style (photo, illustration, etc.). There is no stock-image tool — use `designer_generate_image` or `designer_insert_image` (attached/local file) only.
- For interactive modes, **never** emit `<script>`, inline event handlers, or rely on `<a href>` for navigation between routes — use `data-row-bot-action`.
