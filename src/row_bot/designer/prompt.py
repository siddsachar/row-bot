"""Designer — system prompt builder for designer mode.

Generates the context block injected into the agent's system prompt
when a designer project is active.
"""

from __future__ import annotations

from row_bot.brand import APP_BRAND_ACCENT
from row_bot.designer.components import list_components
from row_bot.designer.state import DesignerProject, normalize_designer_mode


# ── Mode-specific rendering rules ───────────────────────────────────────
# Deck / document modes forbid any JavaScript (sandbox rule unchanged from
# v1). Landing / app_mockup / storyboard modes run inside a controlled
# runtime bridge; the agent must use declarative ``data-row-bot-*`` attrs
# instead of writing free-form <script>.

_DECK_JS_RULE = "- All content must render without JavaScript (sandbox restriction)."

_INTERACTIVE_RUNTIME_RULE = (
    "- Interactive modes use Row-Bot's runtime bridge. NEVER write <script> tags,\n"
    "  onclick/onmouseover/on* inline handlers, or javascript: URLs — they are\n"
    "  stripped automatically and will not run.\n"
    "- Use declarative data attributes for all interactivity:\n"
    "    * data-row-bot-action=\"navigate:<route_id>\" → jump to another screen\n"
    "    * data-row-bot-action=\"toggle_state:<key>\" → flip a UI state\n"
    "    * data-row-bot-action=\"play_media:<asset_id>\" → play a video/audio asset\n"
    "    * data-row-bot-state=\"<key>\"               → mark an element as state-scoped\n"
    "    * data-row-bot-route=\"<route_id>\"          → identify a screen section\n"
    "    * data-row-bot-transition=\"fade|slide_left|slide_up|none\" (optional)\n"
    "- Pair every interactive element (button, link, tap target) with a\n"
    "  data-row-bot-action. Don't rely on <a href>; use data-row-bot-action.\n"
    "- State-scoped overlays: wrap modal/drawer markup in\n"
    "  <div data-row-bot-when=\"cart-open\">...</div>; the runtime will show it\n"
    "  only while <html data-row-bot-state=\"cart-open\"> is set."
)

_INTERACTIVE_TOOL_LINES = (
    "- designer_add_screen: Add a new screen/route (landing or app_mockup modes).\n"
    "- designer_link_screens: Wire a click on one element to navigate to another route.\n"
    "- designer_set_interaction: Generic — attach navigate/toggle_state/play_media to any selector.\n"
    "- designer_preview_screen: Switch the editor's active route to a specific route_id.\n"
    "- designer_reorder_routes: Change the nav order of existing routes.\n"
    "- designer_set_mode: Change project mode (deck/landing/app_mockup/storyboard) — use with care.\n"
)


def _rules_for_mode(mode: str) -> str:
    """Return the rendering-rule line(s) appended to HTML REQUIREMENTS."""
    m = normalize_designer_mode(mode)
    if m in ("landing", "app_mockup", "storyboard"):
        return _INTERACTIVE_RUNTIME_RULE
    return _DECK_JS_RULE


def _mode_guidelines(mode: str) -> str:
    """Return mode-specific guideline lines appended to the main GUIDELINES block.

    Keeps the deck/document default prompt lean but adds explicit
    tool-calling expectations for modes where the agent historically
    replied conversationally instead of editing the project.
    """

    m = normalize_designer_mode(mode)
    if m == "document":
        return (
            "- DOCUMENT MODE: you are authoring a real document (brief, memo,\n"
            "  report, one-pager) — NOT a chat reply.\n"
            "  When the user asks you to \"write\", \"draft\", \"author\", \"create\",\n"
            "  \"put in the document\", \"add to the doc\", or similar, you MUST\n"
            "  emit the content via designer_set_pages (first draft / full rework)\n"
            "  or designer_update_page (targeted edit) — do NOT paste the prose\n"
            "  into the chat reply. A conversational reply containing the document\n"
            "  body counts as a failure.\n"
            "- CONTENT MUST FIT THE CANVAS. Each page is a fixed-size slide with\n"
            "  overflow:hidden — text that does not fit is CLIPPED AND LOST.\n"
            "- WORD BUDGET PER PAGE: for an A4 / letter canvas, keep each page at\n"
            "  roughly 130–160 words of body copy MAX (lead paragraph + 3–4 short\n"
            "  sections with ~2 bullets each).  Count words before you write HTML.\n"
            "  If the user's brief needs more than that, SPLIT into multiple\n"
            "  pages in a single designer_set_pages call (e.g. page 1 = cover +\n"
            "  executive summary, page 2 = details / scope, page 3 = users +\n"
            "  metrics, page 4 = next steps).  Err on the side of shorter copy +\n"
            "  more pages.  A 2-page brief is almost always better than a cramped\n"
            "  1-page brief that clips at the bottom.\n"
            "- LAYOUT BUDGET: leave at least 32–48px of bottom padding inside\n"
            "  the outer container. Use generous line-height (1.45–1.6) and\n"
            "  gap/margin between sections (16–24px). Do not stack more than ~5\n"
            "  top-level sections per page — merge or move a section to the next\n"
            "  page instead.\n"
            "- AFTER generating the first draft, call designer_critique_page on\n"
            "  each new page and, if any finding category is \"overflow\", either\n"
            "  (a) call designer_apply_repairs with categories=[\"overflow\"], or\n"
            "  (b) split content to an additional page via designer_add_page.\n"
            "  Never ship a document with an unresolved overflow finding.\n"
            "- After the tool calls, give a one- or two-sentence chat summary of\n"
            "  what you wrote or changed; never repeat the full document body in\n"
            "  chat. Brief live progress updates while inspecting, editing,\n"
            "  rendering, or validating are allowed, but keep them concise and\n"
            "  do not paste document/page body content into chat.\n"
        )
    if m == "storyboard":
        return (
            "- STORYBOARD MODE: each page is ONE shot (beat + caption + camera).\n"
            "  When the user asks to \"rewrite\", \"update\", \"change\", or \"tighten\"\n"
            "  the shots, you MUST apply the change via designer_update_page for\n"
            "  each affected page — do NOT reply with only prose copy. A\n"
            "  conversational reply without a tool call counts as a failure.\n"
            "- Preserve the two-column shot layout (visual on the left, text on\n"
            "  the right). Update the text inside those columns — do not\n"
            "  restructure the grid.\n"
            "- CONTENT MUST FIT THE CANVAS. The storyboard canvas is a fixed\n"
            "  1920×1080 frame with overflow:hidden — anything past the bottom\n"
            "  is CLIPPED AND LOST.  Keep the right-hand text column lean:\n"
            "    * ONE eyebrow / label (≤ 3 words)\n"
            "    * ONE heading (≤ 6 words)\n"
            "    * ONE short paragraph (≤ 35 words) OR a single pull-quote card\n"
            "    * AT MOST two small metadata cards (e.g. \"Shot type\",\n"
            "      \"Camera move\") OR one voiceover card — not both\n"
            "    * ONE direction / note line (≤ 30 words)\n"
            "    * ONE footer strip (timeline dots OR shot counter)\n"
            "  Do NOT stack production-notes + music + framing + timeline +\n"
            "  voiceover + direction all on one shot — pick 3–4 blocks max.\n"
            "  If more detail is needed, put it in page notes via\n"
            "  designer_generate_notes instead of the visible frame.\n"
            "- LAYOUT BUDGET: the right column should use\n"
            "  display:flex; flex-direction:column; gap:20–28px; justify-content:\n"
            "  center OR flex-start; padding:64px 56px; and leave ≥ 48px of\n"
            "  bottom breathing room. Headings ≤ 2.8rem, body ≤ 1rem.\n"
            "- AFTER updating a shot, call designer_critique_page on that page\n"
            "  and, if any finding is \"overflow\", either (a) call\n"
            "  designer_apply_repairs with categories=[\"overflow\"], or (b)\n"
            "  trim text and re-run the update.  Never ship a clipped shot.\n"
            "- When the user asks for a shot visual (image or short clip), call\n"
            "  designer_generate_image or designer_generate_video; the generated\n"
            "  asset replaces the dashed \"Shot visual\" placeholder automatically.\n"
        )
    if m == "deck":
        return (
            "- DECK MODE: each page is ONE slide on a fixed-size canvas with\n"
            "  overflow:hidden — anything past the bottom is CLIPPED AND LOST.\n"
            "- CONTENT BUDGET PER SLIDE (16:9 @ 1920×1080):\n"
            "    * ONE title / heading line\n"
            "    * EITHER (a) one supporting paragraph ≤ 45 words,\n"
            "      OR (b) up to 3 cards/columns each with a short sub-heading\n"
            "        + ≤ 25 words, OR (c) one chart/image + ≤ 2 bullets.\n"
            "    * Keep bullet lists to ≤ 5 items.  Split into two slides\n"
            "      rather than cramming 8 bullets into one.\n"
            "- LAYOUT BUDGET: use padding of at least 64–96px on every edge.\n"
            "  Leave ≥ 48px of bottom breathing room. Heading ≤ 4.5rem, body\n"
            "  ≤ 1.4rem. Use line-height 1.3–1.5 for body copy.\n"
            "- BUTTONS / CTA rows stay horizontal with gap:16–24px.  Secondary\n"
            "  buttons must be visually distinct (ghost/outline) from primary.\n"
            "- AFTER major rewrites, call designer_critique_page and, if any\n"
            "  finding is \"overflow\", call designer_apply_repairs with\n"
            "  categories=[\"overflow\"] or split the content to a new slide\n"
            "  via designer_add_page.  Never ship a clipped slide.\n"
        )
    if m == "app_mockup":
        return (
            "- APP MOCKUP MODE: keep route_ids stable. Templates wire navigation\n"
            "  through data-row-bot-action=\"navigate:<slug>\" where <slug> is the\n"
            "  page's existing route_id (e.g. \"home\", \"detail\"). Preserve those\n"
            "  route_ids when updating pages; introduce new routes via\n"
            "  designer_add_screen so the runtime bridge can resolve navigation.\n"
            "- Each page is a FULL HTML document. When you call designer_update_page\n"
            "  OR designer_add_page you MUST include the entire <head><style>...\n"
            "  </style></head> block from an existing sibling page, VERBATIM.\n"
            "  Copy the whole stylesheet — don't prune rules that 'look unused';\n"
            "  other selectors (`.topbar`, `.row`, `.title`, `.sub`, `.icon`,\n"
            "  `.toggle`, `.toggle-row`, `.btn`, `.tab`, `.tabbar`, `.screen`,\n"
            "  `.screen-body`, `aria-pressed`) are part of the shared design\n"
            "  system. Partial stylesheets render rows as blue underlined links\n"
            "  with the .title and .sub text collapsed onto one line. When\n"
            "  adding a new screen, call designer_get_page_html on an existing\n"
            "  screen first, then reuse its <head> verbatim.\n"
            "- WIDGET PATTERNS — use these exact markups so the stylesheet and\n"
            "  runtime bridge pick them up:\n"
            "  * Toggle switch row:\n"
            "    <div class=\"toggle-row\"><span>Label</span>"
            "<button class=\"toggle\" aria-pressed=\"true\" "
            "data-row-bot-action=\"toggle_state:<key>\"></button></div>\n"
            "    The <button> MUST be empty; the pill-slider is drawn by CSS via\n"
            "    `.toggle::after` and `[aria-pressed=\"true\"]`. Never put the\n"
            "    label inside the button.\n"
            "  * List row that navigates:\n"
            "    <a class=\"row\" href=\"#\" data-row-bot-action=\"navigate:<slug>\">"
            "<div class=\"icon\">...</div><div><div class=\"title\">...</div>"
            "<div class=\"sub\">...</div></div></a>\n"
            "  * Primary / ghost button:\n"
            "    <a class=\"btn\" href=\"#\" data-row-bot-action=\"navigate:<slug>\">"
            "Label</a>\n"
            "    <a class=\"btn btn-ghost\" href=\"#\" data-row-bot-action=\"...\">"
            "Label</a>\n"
        )
    return ""


def _canvas_rules_for_mode(project: DesignerProject) -> str:
    """Return the CSS-canvas guidance block, conditioned on the project mode.

    Deck / document / storyboard render as fixed-size slide-style pages
    with hard clipping, so the agent must keep content inside the canvas.
    Landing pages are tall scrollable web pages — the stored canvas
    height is a sizing hint, not a viewport; the page must flow
    vertically.  App mockups render one device screen per route at a
    fixed viewport.
    """
    m = normalize_designer_mode(project.mode)
    w = project.canvas_width
    h = project.canvas_height
    if m == "landing":
        return (
            f"- Target viewport width is {w}px (desktop landing page). The stored "
            f"canvas height ({h}px) is a sizing HINT only — landing pages are tall "
            f"scrollable documents, NOT fixed-size slides.\n"
            f"- Set html, body {{ margin:0; width:100%; min-height:100vh; "
            f"overflow-x:hidden; }} and use a centred container like "
            f".page {{ max-width:{w}px; margin:0 auto; padding:0 clamp(16px,4vw,48px); }} "
            f"so content scales on real browsers.\n"
            f"- Let content flow VERTICALLY with normal document flow (flex/grid "
            f"sections stacked). Do NOT lock body to a fixed pixel height, do NOT "
            f"use overflow:hidden on html/body, and avoid absolute positioning on "
            f"layout wrappers — reserve it for decorative orbs/glows inside "
            f"position:relative sections.\n"
            f"- Use responsive units where sensible (rem, %, clamp, vw) so the "
            f"page looks right at widths other than {w}px too.\n"
            f"- Each section (hero, features, pricing, footer, etc.) should feel "
            f"like a real landing-page block with generous padding and breathing room."
        )
    if m == "app_mockup":
        return (
            f"- Each page is ONE device screen at a fixed viewport of {w}×{h}px "
            f"(phone frame unless the brief says otherwise).\n"
            f"- Set html, body {{ margin:0; width:{w}px; height:{h}px; "
            f"overflow:hidden; }} and build the screen as if it were rendered "
            f"inside a real device — status bar at top, tab bar / home indicator "
            f"at bottom where appropriate.\n"
            f"- ALL content must fit within the screen bounds; no horizontal or "
            f"vertical page scroll. Use in-screen scrollable regions (a feed, a "
            f"list) with their own overflow:auto instead.\n"
            f"- Use flexbox or grid for layout; reserve absolute/fixed positioning "
            f"for floating action buttons, sheets, tab bars, status chrome."
        )
    # deck, document, storyboard — fixed-slide semantics
    return (
        f"- Canvas is EXACTLY {w}×{h}px. Set html, body {{ margin:0; "
        f"width:{w}px; height:{h}px; overflow:hidden; }}. ALL content must fit "
        f"within these bounds — do NOT exceed the canvas height.\n"
        f"- Leave at least 48px of bottom padding inside the outer container so "
        f"the last line of copy never touches or crosses the bottom edge.\n"
        f"- Use absolute/fixed positioning or constrained flexbox/grid to keep "
        f"content within bounds. Prefer flex/grid with gap and justify-content "
        f"so blocks distribute evenly instead of crowding at the top.\n"
        f"- Count blocks before writing HTML: if you have more than ~5 stacked "
        f"top-level sections, split across additional pages instead of cramming "
        f"them into one canvas."
    )


def _interactive_tools_block(mode: str) -> str:
    """Return the interactive-mode tool listing (empty string for deck/document)."""
    m = normalize_designer_mode(mode)
    if m in ("landing", "app_mockup", "storyboard"):
        return _INTERACTIVE_TOOL_LINES
    return ""


def _mode_header(mode: str) -> str:
    """Return a human-readable mode label for the prompt header."""
    m = normalize_designer_mode(mode)
    labels = {
        "deck": "slide deck",
        "document": "document",
        "landing": "landing page (interactive, multi-route)",
        "app_mockup": "mobile/desktop app mockup (interactive, multi-screen)",
        "storyboard": "motion storyboard",
    }
    return labels.get(m, "slide deck")


def build_designer_prompt(project: DesignerProject) -> str:
    """Build the designer—mode system prompt injection.

    Includes project metadata, brand config, page list, tool reference,
    and HTML generation guidelines.
    """
    # Brand info
    brand = project.brand
    if brand:
        has_logo = bool((brand.logo_asset_id or "").strip() or brand.logo_b64)
        brand_line = (
            f"Brand: primary={brand.primary_color}, secondary={brand.secondary_color}, "
            f"accent={brand.accent_color}, bg={brand.bg_color}, text={brand.text_color}\n"
            f"       heading_font={brand.heading_font}, body_font={brand.body_font}"
        )
        if has_logo:
            if (brand.logo_mode or "auto") == "manual":
                brand_line += "\n       logo: SET (manual placeholder mode)"
            else:
                scope_label = "all pages" if (brand.logo_scope or "all") == "all" else "first page only"
                brand_line += (
                    f"\n       logo: SET (auto overlay, {scope_label}, {brand.logo_position}, "
                    f"max_height={brand.logo_max_height}px)"
                )
        css_vars = (
            f"  :root {{ --primary: {brand.primary_color}; --secondary: {brand.secondary_color}; "
            f"--accent: {brand.accent_color};\n"
            f"          --bg: {brand.bg_color}; --text: {brand.text_color}; "
            f"--heading-font: {brand.heading_font}; --body-font: {brand.body_font}; }}"
        )
        fonts = f"{brand.heading_font}, {brand.body_font}"
    else:
        brand_line = "Brand: not set (suggest professional defaults)"
        css_vars = (
            f"  :root {{ --primary: {APP_BRAND_ACCENT}; --secondary: #2F4B68; --accent: #5D82A8;\n"
            "          --bg: #0F172A; --text: #F8FAFC; --heading-font: Inter; --body-font: Inter; }"
        )
        fonts = "Inter"

    brief = project.brief
    if brief and not brief.is_empty():
        brief_lines = []
        if brief.output_type:
            brief_lines.append(f"  - Output type: {brief.output_type}")
        if brief.audience:
            brief_lines.append(f"  - Audience: {brief.audience}")
        if brief.tone:
            brief_lines.append(f"  - Tone: {brief.tone}")
        if brief.length:
            brief_lines.append(f"  - Desired length: {brief.length}")
        if brief.build_description:
            brief_lines.append(f"  - What to build: {brief.build_description}")
        if brief.reference_notes:
            brief_lines.append(f"  - References: {brief.reference_notes}")
        if brief.brand_preset:
            brief_lines.append(f"  - Brand preset selected at setup: {brief.brand_preset}")
        if brief.brand_url:
            brief_lines.append(f"  - Brand URL provided at setup: {brief.brand_url}")
        brief_block = "PROJECT BRIEF:\n" + "\n".join(brief_lines)
    else:
        brief_block = "PROJECT BRIEF: not set"

    if project.references:
        reference_lines = []
        for reference in project.references[:12]:
            ref_line = (
                f"  - {reference.id}: {reference.name} "
                f"[{reference.kind}, {reference.size_bytes} bytes]"
            )
            if reference.summary:
                ref_line += f" — {reference.summary}"
            if reference.warnings:
                ref_line += f" (warnings: {len(reference.warnings)})"
            reference_lines.append(ref_line)
        references_block = "AVAILABLE REFERENCES:\n" + "\n".join(reference_lines)
    else:
        references_block = "AVAILABLE REFERENCES: none saved yet"

    component_lines = [
        f"  - {component.name}: {component.description}"
        for component in list_components()
    ]
    components_block = "AVAILABLE CURATED BLOCKS:\n" + "\n".join(component_lines)

    # Page list
    page_lines = []
    for i, page in enumerate(project.pages):
        marker = " ← active" if i == project.active_page else ""
        notes_marker = " · notes" if page.notes.strip() else ""
        page_lines.append(f"  {i}: \"{page.title}\"{notes_marker}{marker}")
    pages_str = "\n".join(page_lines)

    # Manual edits log — actions the user took via the UI since the last turn
    edits = project.manual_edits
    if edits:
        edits_str = "\n".join(f"  • {e}" for e in edits)
        edits_block = (
            f"\nRECENT MANUAL EDITS (user changed the project via the UI):\n"
            f"{edits_str}\n"
        )
        # Clear after including — the LLM only needs to see them once
        project.manual_edits = []
    else:
        edits_block = ""

    logo_instruction = ""
    if brand and bool((brand.logo_asset_id or "").strip() or brand.logo_b64):
        if (brand.logo_mode or "auto") == "manual":
            logo_instruction = (
                f"- LOGO: The brand has a logo set and is in manual placeholder mode. Place the HTML comment <!-- BRAND_LOGO --> wherever "
                f"the logo should appear in the layout. It will be replaced with the actual <img> tag at render time.\n"
            )
        else:
            scope_label = "all pages" if (brand.logo_scope or "all") == "all" else "the first page only"
            position_label = (brand.logo_position or "top_right").replace("_", " ")
            logo_instruction = (
                f"- LOGO: The brand has a logo set and automatic logo placement is active. The logo is already overlaid on {scope_label} at the {position_label} corner. "
                f"Use <!-- BRAND_LOGO --> only when the user explicitly wants a custom in-layout logo placement instead of the automatic overlay.\n"
            )

    mode_label = _mode_header(project.mode)
    interactive_tool_lines = _interactive_tools_block(project.mode)
    rendering_rule = _rules_for_mode(project.mode)
    canvas_rules = _canvas_rules_for_mode(project)
    mode_guidelines = _mode_guidelines(project.mode)

    return (
        f"[DESIGNER MODE]\n"
        f"You are helping the user with a design project: \"{project.name}\"\n"
        f"Project type: {mode_label}\n"
        f"Canvas: {project.canvas_width}×{project.canvas_height} ({project.aspect_ratio})\n"
        f"Published link: {project.publish_url or 'not published yet'}\n"
        f"{brand_line}\n"
        f"{brief_block}\n"
        f"{references_block}\n"
        f"{components_block}\n"
        f"Pages ({len(project.pages)} total):\n{pages_str}\n"
        f"Active page: {project.active_page}\n"
        f"{edits_block}\n"
        f"DESIGNER TOOLS AVAILABLE:\n"
        f"- designer_set_pages: Create/replace ALL pages. Use for new projects or major reworks.\n"
        f"- designer_update_page: Update ONE page. Use for edits to specific pages.\n"
        f"- designer_add_page: Insert a new page. Use index=-1 to append.\n"
        f"- designer_delete_page: Remove a page.\n"
        f"- designer_move_page: Reorder pages.\n"
        f"- designer_get_project: Read current project state, including page summaries and asset IDs.\n"
        f"- designer_get_page_html: Read the full stored HTML for one page before a full-page rewrite.\n"
        f"- designer_get_reference: Read the stored details for one project reference by id or filename.\n"
        f"- designer_generate_notes: Generate speaker notes for one page and save them into the project.\n"
        f"- designer_insert_component: Insert a curated reusable block such as a hero callout, stats band, testimonial, pricing cards, or timeline.\n"
        f"- designer_critique_page: Review the current page for hierarchy, overflow, contrast, readability, and spacing issues.\n"
        f"- designer_apply_repairs: Apply safe deterministic repairs for selected critique categories on the current page.\n"
        f"- designer_set_brand: Update brand colors/fonts.\n"
        f"- designer_resize_project: Resize the canvas for presentation, social, or document presets.\n"
        f"- designer_export: Export as PDF/HTML/PNG/PPTX.\n"
        f"- designer_publish_link: Publish a self-contained HTML deck link through Row-Bot.\n"
        f"- designer_generate_image: Generate an AI image from a text prompt and embed it.\n"
        f"- designer_insert_image: Insert an attached, pasted, generated, or local image into a page.\n"
        f"- designer_move_image: Move an existing inserted image or chart using its asset ID or label.\n"
        f"- designer_replace_image: Replace an existing inserted image or chart using its asset ID or label.\n"
        f"- designer_move_element: Move a section or element using a selector hint, CSS selector, element id, or xpath.\n"
        f"- designer_duplicate_element: Duplicate a section or element and get back a new element id/selector hint.\n"
        f"- designer_restyle_element: Update styles or classes on an existing section or element without rewriting the page.\n"
        f"- designer_refine_text: Refine text on a page (shorten/expand/professional/casual/etc.).\n"
        f"- designer_add_chart: Add a data visualization chart (bar/line/pie/scatter/etc.) to a page.\n"
        f"{interactive_tool_lines}"
        f"\n"
        f"HTML REQUIREMENTS:\n"
        f"- Each page must be a COMPLETE, self-contained HTML document with inline <style>.\n"
        f"- You MUST use these CSS variables in ALL your HTML — never hardcode colors or fonts:\n"
        f"{css_vars}\n"
        f"- Use var(--bg) for backgrounds, var(--text) for text, var(--primary)/var(--secondary)/"
        f"var(--accent) for colored elements, var(--heading-font) for headings, var(--body-font) for body text.\n"
        f"- IMPORTANT: Set body {{ background: var(--bg); color: var(--text); font-family: var(--body-font); }} "
        f"and h1,h2,h3,h4 {{ font-family: var(--heading-font); }} so pages always reflect the brand.\n"
        f"{canvas_rules}\n"
        f"- Include Google Fonts <link> for: {fonts}.\n"
        f"- Use modern CSS: flexbox, grid, gradients. No frameworks needed.\n"
        f"- For placeholder images, use colored divs or SVG shapes, NOT external URLs.\n"
        f"{logo_instruction}"
        f"{rendering_rule}\n"
        f"\n"
        f"GUIDELINES:\n"
        f"- The page list above is ALWAYS the authoritative current state. The user can "
        f"add, delete, or reorder pages via the UI at any time — ignore stale page counts "
        f"from earlier tool results in this conversation.\n"
        f"- When user asks to \"create\" something, use designer_set_pages with all pages.\n"
        f"- When user asks for a specific change, prefer the smallest targeted tool that preserves the existing page.\n"
        f"- Before any full-page rewrite with designer_update_page, call designer_get_page_html for that page so you preserve existing layout and assets.\n"
        f"- Files attached in Designer are persisted as project references. Reuse them across turns and call designer_get_reference when you need the exact extracted content again.\n"
        f"- When the user asks for speaker notes, presenter notes, or a talk track for a page, use designer_generate_notes instead of rewriting the page HTML.\n"
        f"- When the user wants a social, story, document, or other canvas change, use designer_resize_project before reworking layout details.\n"
        f"- When the user wants a shareable link to the deck, use designer_publish_link instead of describing a manual export flow.\n"
        f"- When the user asks for a standard section pattern like metrics, feature cards, testimonials, pricing, or timeline steps, prefer designer_insert_component before writing a custom fragment from scratch.\n"
        f"- When the user asks for review, audit, polish, fix readability, fix contrast, or tighten spacing, call designer_critique_page first and then designer_apply_repairs only for the categories that matter.\n"
        f"- For attached, pasted, generated, or local images, use designer_insert_image instead of recreating the page HTML manually.\n"
        f"- IMAGE PLACEMENT: when authoring a page that will later receive an AI image, mark the target container with `data-row-bot-image-slot=\"NAME\"` (e.g. <div data-row-bot-image-slot=\"hero\" style=\"width:100%;aspect-ratio:16/9;\"></div>). designer_insert_image will automatically fill the first such slot, sized to cover. Alternatively, call designer_insert_image with position=\"replace:.my-class\" or position=\"replace:#my-id\" to target a specific container. Never emit an absolute-positioned image overlay unless the user explicitly asked for a floating element.\n"
        f"- NO DECORATIVE OVERLAP ON TEXT: never place absolutely-positioned decorative CSS art (blobs, hand-drawn shapes, chef figures, mascots, etc.) on top of a heading or body paragraph. If a hero has a headline, either (a) give the illustration its own column/row, (b) put the illustration behind the text with low opacity and z-index:0 AND ensure the text has a readable background or text-shadow, or (c) omit the illustration. When in doubt, use a real AI image via a data-row-bot-image-slot instead of CSS shapes.\n"
        f"- BUTTON ROWS STAY HORIZONTAL: when emitting a row of two buttons (Back + primary, Cancel + Confirm, etc.), use display:flex; gap:12px; and give the primary button flex:1 so they sit side-by-side with clear space between them. Never let them stack vertically, touch each other, or both render with the same primary fill. Secondary/ghost buttons must be visually distinct (transparent background OR an outline) from the primary action.\n"
        f"- For moving or replacing an existing image or chart, use designer_move_image or designer_replace_image with the asset IDs from designer_get_project.\n"
        f"- When you must reuse an existing project image in handwritten HTML, use src=\"asset://ASSET_ID\" and keep data-asset-id=\"ASSET_ID\" on the img when possible. Never invent placeholder tokens like __ASSET_...__.\n"
        f"- For moving, duplicating, or restyling a non-image section or element, use designer_move_element, designer_duplicate_element, or designer_restyle_element with selector hints from designer_get_project.\n"
        f"- If you need a more precise target than the summary provides, call designer_get_page_html and use a CSS selector such as body > section:nth-of-type(1) > div:nth-of-type(2).\n"
        f"- When user says \"make all pages...\", update each page individually.\n"
        f"- Always explain what you changed in your text response.\n"
        f"- If brand colors aren't set, suggest professional defaults.\n"
        f"{mode_guidelines}"
    )
