"""Designer — full-screen Reveal.js presentation mode."""

from __future__ import annotations

import html
import json
import logging
import pathlib
import re
import time
import uuid
from urllib.parse import urlencode
from typing import Optional

from nicegui import ui

from row_bot.designer.publish import ensure_published_dir
from row_bot.designer.state import BrandConfig, DesignerProject
from row_bot.designer.preview import render_page_html
from row_bot.designer.ui_theme import dialog_card_style, style_ghost_button, style_primary_button, style_secondary_button, surface_style

logger = logging.getLogger(__name__)

# Local bundled reveal.js (served via NiceGUI static files)
_REVEAL_CSS = "/static/reveal/reveal.css"
_REVEAL_JS = "/static/reveal/reveal.js"
_PRESENTATION_RUNTIME_DIR = "_runtime"
_SLIDES_WINDOW_WIDTH = 1600
_SLIDES_WINDOW_HEIGHT = 900


def _build_reveal_html(project: DesignerProject, start_page: int = 0, presenter: bool = False) -> str:
    """Build a self-contained Reveal.js HTML document from project pages."""
    brand = project.brand or BrandConfig()

    # Get font CSS for the brand fonts (local first)
    from row_bot.designer.fonts import get_all_fonts_css, get_fallback_stack
    families = list(dict.fromkeys([brand.heading_font, brand.body_font]))
    font_css = get_all_fonts_css(families)
    h_fallback = get_fallback_stack(brand.heading_font)
    b_fallback = get_fallback_stack(brand.body_font)

    # Build individual slides
    slides_html = []
    for page_index, page in enumerate(project.pages):
        html_content = render_page_html(project, page.html, page_index=page_index)
        safe_srcdoc = html.escape(html_content, quote=True)

        notes_html = ""
        if page.notes:
            safe_notes = page.notes.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            notes_html = f'<aside class="notes">{safe_notes}</aside>'

        slides_html.append(f"""
            <section data-background-color="{brand.bg_color}">
                <div class="slide-frame-shell" style="width: {project.canvas_width}px; height: {project.canvas_height}px;">
                    <iframe
                        class="slide-frame"
                        sandbox="allow-same-origin allow-scripts"
                        scrolling="no"
                        tabindex="-1"
                        srcdoc="{safe_srcdoc}"
                    ></iframe>
                </div>
                {notes_html}
            </section>
        """)

    all_slides = "\n".join(slides_html)
    slide_meta = json.dumps([
        {"title": page.title, "notes": page.notes}
        for page in project.pages
    ])
    stage_wrapper = (
        f"""
    <div class="presenter-shell">
        <div class="presenter-stage">
            <div class="reveal">
                <div class="slides">
                    {all_slides}
                </div>
            </div>
        </div>
        <aside class="presenter-sidebar">
            <div>
                <div class="presenter-eyebrow">Presenter View</div>
                <div id="presenter-slide-title" class="presenter-title"></div>
                <div id="presenter-counter" class="presenter-meta"></div>
            </div>
            <div class="presenter-panel">
                <div class="presenter-label">Speaker notes</div>
                <div id="presenter-notes" class="presenter-notes"></div>
            </div>
            <div class="presenter-panel">
                <div class="presenter-label">Next slide</div>
                <div id="presenter-next-title" class="presenter-next"></div>
            </div>
            <div class="presenter-footer">
                <span id="presenter-timer">00:00</span>
                <span>Use Back to Editor to end the session</span>
            </div>
        </aside>
    </div>
"""
        if presenter else
        f"""
    <div class="reveal">
        <div class="slides">
            {all_slides}
        </div>
    </div>
"""
    )

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="{_REVEAL_CSS}">
    <style>
        {font_css}
        body {{
            margin: 0;
            overflow: hidden;
            background: {brand.bg_color};
        }}
        .reveal .slides section {{
            display: flex !important;
            align-items: center;
            justify-content: center;
            padding: 0;
        }}
        .reveal {{
            width: 100%;
            height: 100vh;
            background: {brand.bg_color};
        }}
        .slide-frame-shell {{
            position: relative;
            overflow: hidden;
            background: transparent;
        }}
        .slide-frame {{
            width: 100%;
            height: 100%;
            border: none;
            display: block;
            background: transparent;
            /* pointer-events must stay enabled so <video controls> and other
               interactive slide content respond to clicks in the deck. */
            pointer-events: auto;
        }}
        body.presenter-mode {{
            display: flex;
            background: #08111f;
            color: #e2e8f0;
            font-family: Arial, sans-serif;
        }}
        .presenter-shell {{
            display: flex;
            width: 100vw;
            height: 100vh;
        }}
        .presenter-stage {{
            flex: 1 1 auto;
            min-width: 0;
        }}
        .presenter-stage .reveal {{
            height: 100%;
        }}
        .presenter-sidebar {{
            width: 360px;
            box-sizing: border-box;
            padding: 20px;
            border-left: 1px solid rgba(255,255,255,0.12);
            background: rgba(8, 17, 31, 0.96);
            display: flex;
            flex-direction: column;
            gap: 14px;
        }}
        .presenter-eyebrow {{
            font-size: 12px;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: #94a3b8;
            margin-bottom: 8px;
        }}
        .presenter-title {{
            font-size: 22px;
            font-weight: 700;
            line-height: 1.2;
            margin-bottom: 6px;
        }}
        .presenter-meta {{
            font-size: 13px;
            color: #94a3b8;
        }}
        .presenter-panel {{
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 12px;
            padding: 14px;
            background: rgba(15, 23, 42, 0.92);
        }}
        .presenter-label {{
            font-size: 12px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #94a3b8;
            margin-bottom: 10px;
        }}
        .presenter-notes {{
            white-space: pre-wrap;
            line-height: 1.5;
            font-size: 14px;
            max-height: 52vh;
            overflow: auto;
        }}
        .presenter-next {{
            font-size: 16px;
            font-weight: 600;
            line-height: 1.35;
        }}
        .presenter-footer {{
            margin-top: auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 13px;
            color: #94a3b8;
        }}
    </style>
</head>
<body class="{'presenter-mode' if presenter else ''}">
    {stage_wrapper}
    <script src="{_REVEAL_JS}"></script>
    <script>
        const cw = {project.canvas_width};
        const ch = {project.canvas_height};
        const presenterMode = {str(presenter).lower()};
        const slideMeta = {slide_meta};
        const params = new URLSearchParams(window.location.search);
        const presentationSessionId = params.get('session') || '';
        const presentationWindowKey = params.get('window') || '';
        const syncChannelName = presentationSessionId ? `row-bot-designer-presentation-${{presentationSessionId}}` : '';
        const syncStorageKey = syncChannelName ? `${{syncChannelName}}:message` : '';
        const syncChannel = syncChannelName && 'BroadcastChannel' in window ? new BroadcastChannel(syncChannelName) : null;
        const syncInstanceId = `${{presenterMode ? 'presenter' : 'slides'}}-${{Math.random().toString(36).slice(2)}}`;
        let suppressSyncBroadcast = 0;

        function currentSlideIndex() {{
            return ((Reveal.getIndices() || {{ h: 0 }}).h || 0);
        }}

        function publishSessionMessage(message) {{
            if (!presentationSessionId) return;
            const payload = Object.assign({{
                source: syncInstanceId,
                presenter: presenterMode,
                ts: Date.now(),
            }}, message || {{}});
            if (syncChannel) {{
                syncChannel.postMessage(payload);
                return;
            }}
            if (!syncStorageKey) return;
            try {{
                localStorage.setItem(syncStorageKey, JSON.stringify(payload));
            }} catch (err) {{
                console.warn('presentation sync storage failed', err);
            }}
        }}

        function applyRemoteSlide(index) {{
            if (typeof index !== 'number') return;
            if (currentSlideIndex() === index) return;
            suppressSyncBroadcast += 1;
            Reveal.slide(index);
        }}

        function handleSessionMessage(message) {{
            if (!message || message.source === syncInstanceId) return;
            if (message.type === 'request-state') {{
                publishSessionMessage({{ type: 'slide-state', index: currentSlideIndex() }});
                return;
            }}
            if (message.type === 'slide-state') {{
                applyRemoteSlide(message.index);
            }}
        }}

        if (syncChannel) {{
            syncChannel.onmessage = function(event) {{
                handleSessionMessage(event.data);
            }};
        }} else if (syncStorageKey) {{
            window.addEventListener('storage', function(event) {{
                if (event.key !== syncStorageKey || !event.newValue) return;
                try {{
                    handleSessionMessage(JSON.parse(event.newValue));
                }} catch (err) {{
                    console.warn('presentation sync parse failed', err);
                }}
            }});
        }}

        Reveal.initialize({{
            hash: false,
            controls: true,
            progress: true,
            center: true,
            transition: 'slide',
            width: cw,
            height: ch,
            margin: 0,
            showNotes: false,
        }});

        function updatePresenterSidebar() {{
            if (!presenterMode) return;
            const idx = (Reveal.getIndices() || {{h: 0}}).h || 0;
            const current = slideMeta[idx] || {{}};
            const next = slideMeta[idx + 1] || null;
            const titleEl = document.getElementById('presenter-slide-title');
            const counterEl = document.getElementById('presenter-counter');
            const notesEl = document.getElementById('presenter-notes');
            const nextEl = document.getElementById('presenter-next-title');
            if (titleEl) titleEl.textContent = current.title || `Slide ${{idx + 1}}`;
            if (counterEl) counterEl.textContent = `Slide ${{idx + 1}} of ${{slideMeta.length}}`;
            if (notesEl) notesEl.textContent = current.notes || 'No speaker notes for this slide yet.';
            if (nextEl) nextEl.textContent = next ? (next.title || `Slide ${{idx + 2}}`) : 'End of deck';
        }}

        if (presenterMode) {{
            const startedAt = Date.now();
            window.setInterval(function() {{
                const elapsed = Math.floor((Date.now() - startedAt) / 1000);
                const mins = String(Math.floor(elapsed / 60)).padStart(2, '0');
                const secs = String(elapsed % 60).padStart(2, '0');
                const timerEl = document.getElementById('presenter-timer');
                if (timerEl) timerEl.textContent = `${{mins}}:${{secs}}`;
            }}, 1000);
        }}

        // ── Slide media autoplay ──────────────────────────────────
        // The slide iframes are sandboxed with allow-same-origin so the
        // parent can reach into their DOM and drive <video>/<audio>
        // playback on slide change. Videos authored in the editor ship
        // with autoplay=false + muted=true + controls=true, so we play
        // muted here (Chrome/Safari permit muted autoplay) and let the
        // presenter unmute via the controls if desired.
        function _eachMediaIn(iframe, fn) {{
            if (!iframe) return;
            var doc = null;
            try {{ doc = iframe.contentDocument; }} catch (_) {{ return; }}
            if (!doc) return;
            var nodes = doc.querySelectorAll('video, audio');
            for (var i = 0; i < nodes.length; i++) {{
                try {{ fn(nodes[i]); }} catch (_) {{ /* ignore */ }}
            }}
        }}
        function pauseAllSlideMedia() {{
            var frames = document.querySelectorAll('.slide-frame');
            for (var i = 0; i < frames.length; i++) {{
                _eachMediaIn(frames[i], function(m) {{
                    try {{ m.pause(); m.currentTime = 0; }} catch (_) {{}}
                }});
            }}
        }}
        function playActiveSlideMedia() {{
            var current = null;
            try {{ current = Reveal.getCurrentSlide(); }} catch (_) {{ return; }}
            if (!current) return;
            var frame = current.querySelector('.slide-frame');
            if (!frame) return;
            // The iframe's document may not be ready yet on 'ready' event;
            // retry a couple of times before giving up.
            var attempts = 0;
            (function tryPlay() {{
                attempts += 1;
                var playedAny = false;
                _eachMediaIn(frame, function(m) {{
                    // Muted is required for cross-browser autoplay; slides
                    // with autoplay already set are respected, others are
                    // played muted by default on slide show.
                    if (!m.hasAttribute('muted')) m.muted = true;
                    var p = m.play();
                    if (p && typeof p.catch === 'function') p.catch(function() {{}});
                    playedAny = true;
                }});
                if (!playedAny && attempts < 5) {{
                    window.setTimeout(tryPlay, 150);
                }}
            }})();
        }}

        Reveal.on('ready', function() {{
            updatePresenterSidebar();
            playActiveSlideMedia();
            if (presenterMode) {{
                publishSessionMessage({{ type: 'slide-state', index: currentSlideIndex() }});
            }} else {{
                publishSessionMessage({{ type: 'request-state' }});
            }}
        }});

        Reveal.on('slidechanged', function() {{
            updatePresenterSidebar();
            pauseAllSlideMedia();
            playActiveSlideMedia();
            if (suppressSyncBroadcast > 0) {{
                suppressSyncBroadcast -= 1;
                return;
            }}
            publishSessionMessage({{ type: 'slide-state', index: currentSlideIndex() }});
        }});

        // Jump to requested start page
        const startIdx = {start_page};
        if (startIdx > 0) {{
            Reveal.slide(startIdx);
        }}
    </script>
</body>
</html>"""


def _ensure_presentation_runtime_dir() -> pathlib.Path:
    runtime_dir = ensure_published_dir() / _PRESENTATION_RUNTIME_DIR
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir


def _write_presentation_document(
    project: DesignerProject,
    *,
    start_page: int = 0,
    presenter: bool = False,
) -> str:
    runtime_dir = _ensure_presentation_runtime_dir()
    variant = "presenter" if presenter else "slides"
    path = runtime_dir / f"{project.id}-{variant}.html"
    path.write_text(
        _build_reveal_html(project, start_page=start_page, presenter=presenter),
        encoding="utf-8",
    )
    cache_buster = time.time_ns()
    return f"/published/{_PRESENTATION_RUNTIME_DIR}/{path.name}?v={cache_buster}"


def _build_presentation_source_url(
    project: DesignerProject,
    *,
    start_page: int,
    presenter: bool,
    session_id: str,
    window_key: str,
) -> str:
    base_url = _write_presentation_document(project, start_page=start_page, presenter=presenter)
    query = urlencode({"session": session_id, "window": window_key})
    return f"{base_url}&{query}" if "?" in base_url else f"{base_url}?{query}"


async def _open_managed_window(*, url: str, name: str, title: str, width: int, height: int) -> bool:
    script = f"""
        (async function() {{
            if (!window.rowBotOpenManagedWindow) return false;
            return await window.rowBotOpenManagedWindow({json.dumps({
                'url': url,
                'name': name,
                'title': title,
                'width': width,
                'height': height,
            })});
        }})()
    """
    try:
        result = await ui.run_javascript(script)
        return bool(result)
    except Exception:
        logger.exception("Failed to open managed presentation window")
        return False


async def _close_managed_window(name: str) -> bool:
    script = f"""
        (async function() {{
            if (!window.rowBotCloseManagedWindow) return false;
            return await window.rowBotCloseManagedWindow({json.dumps(name)});
        }})()
    """
    try:
        result = await ui.run_javascript(script)
        return bool(result)
    except Exception:
        logger.exception("Failed to close managed presentation window")
        return False


async def show_presentation(
    project: DesignerProject,
    start_page: Optional[int] = None,
    presenter: bool = False,
) -> None:
    """Open the presenter surface in Thoth and slides in a separate window."""
    page_idx = start_page if start_page is not None else project.active_page
    session_id = uuid.uuid4().hex
    slides_window_key = f"row-bot-designer-slides-{project.id}-{session_id}"
    presenter_window_key = f"row-bot-designer-presenter-{project.id}-{session_id}"
    presenter_url = _build_presentation_source_url(
        project,
        start_page=page_idx,
        presenter=True,
        session_id=session_id,
        window_key=presenter_window_key,
    )
    slides_url = _build_presentation_source_url(
        project,
        start_page=page_idx,
        presenter=False,
        session_id=session_id,
        window_key=slides_window_key,
    )
    slides_title = f"Thoth Presentation — {project.name}"
    slides_opened = await _open_managed_window(
        url=slides_url,
        name=slides_window_key,
        title=slides_title,
        width=_SLIDES_WINDOW_WIDTH,
        height=_SLIDES_WINDOW_HEIGHT,
    )

    status_text = (
        "Slides opened in a separate window. Move that window to the audience display and present from here."
        if slides_opened
        else "Slides did not open automatically. Use Open Slides Window to launch them separately."
    )

    with ui.dialog().props("maximized persistent") as dlg, ui.card().classes("w-full h-full").style(
        dialog_card_style(max_width="calc(100vw - 20px)", height="calc(100vh - 20px)", padding="18px")
    ):
        with ui.column().classes("w-full h-full no-wrap").style("gap: 14px;"):
            with ui.row().classes("w-full items-start justify-between").style("gap: 14px;"):
                with ui.column().classes("gap-1"):
                    ui.label("Present").classes("text-h5 text-weight-bold")
                    status_label = ui.label(status_text).classes("text-sm text-grey-4")

                with ui.row().classes("items-center").style("gap: 10px;"):
                    async def _reopen_slides() -> None:
                        reopened = await _open_managed_window(
                            url=slides_url,
                            name=slides_window_key,
                            title=slides_title,
                            width=_SLIDES_WINDOW_WIDTH,
                            height=_SLIDES_WINDOW_HEIGHT,
                        )
                        status_label.set_text(
                            "Slides opened in a separate window. Move that window to the audience display and present from here."
                            if reopened
                            else "Slides still could not be opened automatically. Check popup permissions or reopen from native mode."
                        )

                    async def _end_session() -> None:
                        await _close_managed_window(slides_window_key)
                        dlg.close()

                    reopen_btn = ui.button("Open Slides Window", icon="open_in_new", on_click=_reopen_slides)
                    style_primary_button(reopen_btn, compact=True)
                    back_btn = ui.button("Back to Editor", icon="arrow_back", on_click=_end_session)
                    style_secondary_button(back_btn, compact=True)
                    close_btn = ui.button(icon="close", on_click=_end_session).props("flat dense round")
                    style_ghost_button(close_btn, compact=True)

            with ui.card().classes("w-full flex-grow").style(surface_style(padding="0", strong=True) + " min-height: 0; overflow: hidden;"):
                ui.html(
                    f'<iframe id="designer-presenter-frame" src="{html.escape(presenter_url, quote=True)}" '
                    'style="width: 100%; height: 100%; border: none;" '
                    'allowfullscreen></iframe>',
                    sanitize=False,
                ).style("width: 100%; height: 100%;")

    dlg.open()
