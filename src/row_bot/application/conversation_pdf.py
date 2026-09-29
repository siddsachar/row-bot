"""Conversation export as PDF, from the same saved transcript as the Markdown copy.

The transcript is escaped before anything else, so no message can add markup,
scripts or remote resources. Chromium renders it with JavaScript off and every
request refused (the Designer's strict export), and a plain-text PDF is made
when that runtime is not installed.
"""
from __future__ import annotations

import concurrent.futures
import html
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_CSS = """
@page { size: A4; margin: 16mm 16mm 16mm 16mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: 'Segoe UI', -apple-system, Roboto, Helvetica, Arial, sans-serif;
       font-size: 10.5pt; line-height: 1.45; color: #1f2328; margin: 0; }
header { border-bottom: 1px solid #d0d7de; padding-bottom: 8px; margin-bottom: 14px; }
header h1 { font-size: 17pt; margin: 0 0 2px; }
header p { font-size: 9pt; color: #656d76; margin: 0; }
h2 { font-size: 10.5pt; margin: 14px 0 4px; break-after: avoid; }
h2.you { color: #0b57d0; }
h2.bot { color: #8a5a00; }
h3 { font-size: 10.5pt; margin: 10px 0 4px; }
p { margin: 4px 0; overflow-wrap: anywhere; }
ul { margin: 4px 0; padding-left: 20px; }
pre { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 4px; padding: 8px 10px;
      font: 9pt Consolas, 'Courier New', monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
code { font-family: Consolas, 'Courier New', monospace; background: #f6f8fa; padding: 0 3px;
       border-radius: 3px; }
"""

_INLINE = (
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
)


def _inline(text: str) -> str:
    value = html.escape(text, quote=True)
    for pattern, replacement in _INLINE:
        value = pattern.sub(replacement, value)
    return value


def transcript_html(title: str, markdown: str, *, now: datetime | None = None) -> str:
    """A print page for one exported transcript; every message is escaped."""
    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []
    code: list[str] | None = None

    def flush() -> None:
        if paragraph:
            blocks.append("<p>" + "<br>".join(_inline(line) for line in paragraph) + "</p>")
            paragraph.clear()
        if items:
            blocks.append("<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + "</ul>")
            items.clear()

    lines = markdown.splitlines()
    # The Markdown export's own heading and note become the page header.
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    while lines and (not lines[0].strip() or lines[0].startswith("_Exported from")):
        lines = lines[1:]
    for line in lines:
        if line.lstrip().startswith("```"):
            if code is None:
                flush()
                code = []
            else:
                blocks.append("<pre>" + html.escape("\n".join(code)) + "</pre>")
                code = None
            continue
        if code is not None:
            code.append(line)
        elif line in ("## You", "## Row-Bot"):
            flush()
            role = line[3:]
            blocks.append(f'<h2 class="{"you" if role == "You" else "bot"}">{role}</h2>')
        elif re.match(r"^#{1,6} ", line):
            flush()
            blocks.append(f"<h3>{_inline(line.lstrip('#').strip())}</h3>")
        elif re.match(r"^\s*[-*] ", line):
            if paragraph:
                flush()
            items.append(re.sub(r"^\s*[-*] ", "", line))
        elif not line.strip():
            flush()
        else:
            if items:
                flush()
            paragraph.append(line)
    if code is not None:
        blocks.append("<pre>" + html.escape("\n".join(code)) + "</pre>")
    flush()
    stamp = (now or datetime.now()).strftime("%d %B %Y, %H:%M").lstrip("0")
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>"
        f"<header><h1>{html.escape(title)}</h1><p>Exported from Row-Bot on {stamp}</p></header>"
        + "".join(blocks)
        + "</body></html>"
    )


def _render_chromium(page_html: str) -> bytes:
    from playwright.sync_api import sync_playwright

    from row_bot.browser.runtime import playwright_chromium_launch_options

    def work() -> bytes:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_chromium_launch_options())
            try:
                context = browser.new_context(java_script_enabled=False, service_workers="block",
                                              accept_downloads=False)
                context.set_default_timeout(15000)
                context.route("**/*", lambda route: route.abort())
                page = context.new_page()
                page.set_content(page_html, wait_until="load")
                page.emulate_media(media="print")
                return page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
            finally:
                browser.close()

    # The sync API refuses a thread that runs an event loop; use a fresh one.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(work).result()


_FONTS = (
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
)


def _render_plain(title: str, markdown: str, *, fonts: tuple[Path, ...] = _FONTS) -> bytes:
    """Text-only PDF with fpdf2 (Designer extra) when Chromium is not installed."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    font = next((path for path in fonts if path.is_file()), None)
    if font is not None:
        pdf.add_font("Body", "", str(font))
        family = "Body"

        def safe(text: str) -> str:
            return text
    else:
        family = "Helvetica"

        def safe(text: str) -> str:
            return text.encode("latin-1", errors="replace").decode("latin-1")

    pdf.set_font(family, size=16)
    pdf.multi_cell(0, 8, safe(title), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    lines = markdown.splitlines()[1:]
    for line in lines:
        if line.startswith("_Exported from"):
            continue
        if line in ("## You", "## Row-Bot"):
            pdf.ln(2)
            pdf.set_font(family, size=11)
            if line == "## You":
                pdf.set_text_color(11, 87, 208)
            else:
                pdf.set_text_color(138, 90, 0)
            pdf.multi_cell(0, 6, line[3:], new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(31, 35, 40)
            pdf.set_font(family, size=10)
            continue
        pdf.multi_cell(0, 5, safe(line) or " ", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


class PdfUnavailable(RuntimeError):
    """Neither Chromium nor fpdf2 is installed."""


def render_pdf(title: str, markdown: str) -> bytes:
    """PDF bytes for one exported transcript (never touches the network)."""
    try:
        return _render_chromium(transcript_html(title, markdown))
    except Exception as exc:
        logger.info("Conversation PDF: Chromium render unavailable (%s); using plain text", type(exc).__name__)
    try:
        return _render_plain(title, markdown)
    except ImportError:
        raise PdfUnavailable from None
