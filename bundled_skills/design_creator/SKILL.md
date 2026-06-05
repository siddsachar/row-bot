---
name: design_creator
display_name: Design Creator
icon: "🎨"
description: Structured workflow for creating professional designs — slide decks, documents, landing pages, app mockups, and motion storyboards.
enabled_by_default: true
version: "1.0"
tags:
  - design
  - presentations
  - marketing
activation:
  phrases:
    - one-page product concept
    - product concept
    - create a one-pager
    - landing page
    - slide deck
  keywords:
    - design
    - product
    - concept
    - one-page
    - one-pager
    - landing
    - deck
  negative_phrases:
    - meeting notes
    - research competitors
  examples:
    - Create a one-page product concept
author: Row-Bot
---
When the user asks you to **create a presentation**, **design a slide deck**,
**make marketing material**, **build a one-pager**, **design a wireframe**,
**design a landing page**, or **prototype an app**,
follow this structured approach:

## 0. Pick the right MODE first

Designer projects have a `mode`. The mode dictates layout, canvas, available templates, and which export targets make sense. Always lock the mode before structuring content.

| User's intent | Mode | Default canvas |
|---|---|---|
| pitch deck, presentation, slide deck | `deck` | 16:9 |
| report, one-pager, brief, document | `document` | A4 |
| marketing site, hero page, product page | `landing` | landing (1440×3200, scrollable) |
| mobile app prototype, click-through wireframe | `app_mockup` | phone (390×844) — or `desktop` (1440×900) |
| video shot list, scene-by-scene board | `storyboard` | 16:9 |

If the request is ambiguous (e.g. "design something for our launch"), ask ONE clarifying question to choose between deck / landing / app_mockup. If the project already exists, read its mode with `designer_get_project` and only switch via `designer_set_mode` if the new task genuinely belongs to a different mode.

## 1. Understand the Brief

Before generating anything, clarify:
- **Purpose**: What is this for? (investor pitch, team update, product launch, etc.)
- **Audience**: Who will see it? (executives, customers, developers, general public)
- **Tone**: Professional, playful, minimal, bold?
- **Length**: How many slides/pages? (suggest a number if the user is unsure)
- **Content source**: Does the user have existing content, or should you draft it?

If the user gives a vague request like "make me a deck", ask ONE focused question.
If they give a clear brief, proceed immediately — don't over-question.

## 2. Structure the Content

Before designing, outline the content. The structure depends on the mode:
- **Presentations (`deck`)**: Title → Problem → Solution → Key Features → Proof/Data → Team → CTA
- **One-Pagers / Reports (`document`)**: Header → Value Prop → 3 Key Points → Social Proof → CTA  *or*  Title → Executive Summary → Sections with Data → Conclusion → Next Steps
- **Marketing email / static asset (`deck` 1:1 or 9:16)**: Headline → Subhead → Benefits Grid → Testimonial → CTA
- **Landing pages (`landing`)**: Nav → Hero (headline + subhead + primary CTA) → 3-feature row → Social proof / logos → Pricing tiers → FAQ → Footer CTA. One tall scrollable page is the norm.
- **App mockups (`app_mockup`)**: List every screen first (Home / Detail / Settings / Onboarding / etc.). Each screen = one route = one page. Define the navigation graph (Home row tap → Detail; tab bar → Home/Search/Profile; back button → previous route).
- **Storyboards (`storyboard`)**: 4–8 shots, each with a beat description, on-screen caption, and camera direction (e.g. "wide push-in", "handheld OTS").
- **Wireframes**: low-fidelity layout with placeholder sections, buttons, nav structure (use `deck` or `app_mockup` depending on whether you need click-through).

Share the outline with the user before generating HTML if there are more than 3 pages, OR for any `app_mockup` (so the route graph is approved before wiring).

## 3. Design Principles

Apply these when generating designs:
- **Visual hierarchy**: Largest element = most important. Use size, weight, and color to guide the eye.
- **Whitespace**: Don't crowd elements. Breathing room makes designs look professional.
- **Consistency**: Same fonts, colors, spacing, and layout patterns across all pages.
- **Contrast**: Ensure text is readable against backgrounds. WCAG AA minimum.
- **Alignment**: Use CSS grid or flexbox. No randomly positioned elements.
- **Typography**: 2 fonts max (one heading, one body). Size ratio: heading ≥ 2× body.
- **Color**: Use brand colors via CSS variables. 60-30-10 rule (primary-secondary-accent).

## 3b. Anti-clipping content budgets (fixed-slide modes)

`deck`, `document`, and `storyboard` canvases clip with `overflow:hidden`. Keep content lean:

- **`deck` slide**: ONE heading + EITHER one paragraph (≤45 words), up to 3 cards (≤25 words each), OR chart/image + ≤2 bullets. Max 5 bullets. 64–96 px edge padding, heading ≤4.5rem, body ≤1.4rem. Leave ≥48 px bottom breathing room.
- **`document` page**: 130–160 words body copy. Max ~5 top-level sections. 16–24 px gap, line-height 1.45–1.6, ≥32–48 px bottom padding. Split pages rather than cramming.
- **`storyboard` shot**: one eyebrow + one heading (≤6 words) + one paragraph (≤35 words) OR quote + ≤2 metadata cards OR one voiceover card + one direction line + one footer strip. 3–4 visible blocks MAX. Put extra detail in page notes via `designer_generate_notes`.

Count blocks BEFORE writing HTML. A 2-page brief beats a cramped 1-page brief that clips.

## 3c. Authoring guardrails (all modes)

- **No decorative overlap on text.** Don't place absolutely-positioned CSS art (blobs, mascots, chef figures, illustrated shapes) on top of headings or body copy. Give it its own column/row, push it behind with `opacity` + `z-index:0` and a readable text background, or omit it. For real imagery, use a typed image slot.
- **Button rows stay horizontal.** Two buttons side-by-side use `display:flex; gap:12–24px;` with `flex:1` on the primary. Secondary/ghost buttons must be visibly distinct from the primary (ghost/outline) — never two identical filled pills.
- **Typed image slots over overlays.** When a page will receive an AI image, author the target as `<div data-row-bot-image-slot="NAME" style="width:100%;aspect-ratio:16/9;"></div>`. `designer_generate_image` and `designer_insert_image` fill it automatically, sized to cover. Or pass `position="replace:.my-class"` to target a specific container. Never ship a floating absolute-positioned overlay unless the user asked for one.

## 3d. Critique-repair loop (mandatory after rewrites)

After any full-page rewrite, mode switch, canvas resize, or multi-page update:
1. `designer_critique_page(page_index=<n>)` per changed page.
2. If any finding category is `overflow`, either `designer_apply_repairs(categories=["overflow"])`, trim + update, or split via `designer_add_page`.
3. Never ship a page with an unresolved `overflow` finding.

Before publishing or exporting, also run `designer_brand_lint` for a quick contrast / off-palette / missing-alt sweep.

## 4. Iterate Effectively

After initial generation:
- Ask "Would you like me to adjust anything?" instead of assuming it's perfect.
- When the user gives vague feedback ("make it better"), ask what specifically feels off.
- For broad changes ("make everything more modern"), update all pages consistently.
- For specific changes ("make the title bigger on slide 3"), update only that page.
- Offer alternatives: "I can try a dark version or a minimal version — which interests you?"

## 4b. Interactive design rules (`landing` and `app_mockup` only)

These two modes use a sandboxed runtime — **never write `<script>` tags or inline `onclick=`**. Wire interactivity declaratively:

- Mark each screen's outer container with `data-row-bot-route="<route_id>"`.
- For navigation, put `data-row-bot-action="navigate:<route_id>"` on the clickable element. Optional `data-row-bot-transition="slide_left|slide_right|slide_up|fade|none"`.
- For UI state toggles (dark mode, expanded panel, notifications on/off), use `data-row-bot-action="toggle_state:<key>"` and style the on-state with `[data-row-bot-state-<key>="on"]` selectors on `<html>`.
- For media playback buttons, use `data-row-bot-action="play_media:<asset_id>"`.

Prefer the dedicated tools over hand-editing these attributes:
- `designer_add_screen` to add a new route.
- `designer_link_screens(source_route, selector, target_route)` to wire navigation.
- `designer_set_interaction(source_route, selector, action, target, ...)` for toggles and media.
- `designer_reorder_routes` to change the route order.
- `designer_preview_screen` to view one route in isolation.

For app mockups: ONE screen per page, fixed device viewport on the body, scrolling happens inside an inner `.screen-body{overflow-y:auto}`.
For landing pages: ONE tall scrollable page, `max-width:1440px` content wrapper, NEVER set `body{overflow:hidden}` or a fixed pixel height.

## 5. Polish & Export

Before delivering:
- Check all pages for consistent branding (colors, fonts, spacing).
- Ensure text is readable at the canvas size.
- Verify no placeholder content was left in.
- For interactive modes: click through every wired path in `designer_preview_screen` (or the in-app preview) to confirm navigation works.
- Run `designer_brand_lint` for a quick contrast / off-palette / missing-alt sweep before publishing.
- Suggest export format based on mode and use case:
  - `deck` → PDF (print/share), PPTX (editable), PNG (single slides)
  - `document` → PDF (canonical), HTML (web)
  - `landing` → **`designer_publish_link`** (interactive bundle with runtime + transitions) or `designer_export(format="html")` for a single self-contained file
  - `app_mockup` → **`designer_publish_link`** (interactive click-through prototype URL) — this is almost always what the user wants
  - `storyboard` → PDF (shot list) or PNG (per-shot frames)
