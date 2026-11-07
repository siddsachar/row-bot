"""A web page's main content as Markdown: what ``read_url`` returns for an HTML page (#139).

Local and small on purpose, BeautifulSoup only: nothing is sent to a third-party reader. The rules:

1. Drop page furniture: scripts, styles, forms, navigation, sidebars, hidden parts, and the page's own
   header and footer (an article's header, with its title, stays).
2. Keep the main region: the first ``<main>``, ``<article>`` or ``[role=main]`` holding most of the
   text, else the whole body.
3. Write it as Markdown: headings, lists, links with absolute URLs, code, simple tables, and a line
   break after every block so words never run together.

A page that builds itself with JavaScript has little text here; it says so and points to the browser.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import PreformattedString

_FURNITURE = ("title, script, style, noscript, template, svg, nav, aside, form, button, iframe, "
              "[hidden], [aria-hidden=true], [role=navigation], [role=dialog]")
_MAIN = "main, article, [role=main]"
_BLOCKS = {"p", "div", "section", "article", "main", "header", "footer", "figure", "figcaption",
           "details", "summary", "dl", "dt", "dd", "address", "table", "tr", "td", "th", "caption"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_INDENT = "\x00"  # one level of a nested list; spaces would be tidied away before the end
_SHORT = 200
JAVASCRIPT_ONLY = ("[This page builds itself with JavaScript, so its text isn't in the page. "
                   "Open it with the browser (browser_navigate) to read it.]")


def readable_markdown(soup: BeautifulSoup, base_url: str) -> str:
    """The page's title and main content as Markdown; empty when it has no text at all."""
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    scripted = soup.find("script") is not None
    for tag in soup.select(_FURNITURE + ", header, footer"):
        if not tag.decomposed and (tag.name not in {"header", "footer"} or not tag.find_parent(["main", "article"])):
            tag.decompose()
    page = soup.body or soup
    total = len(page.get_text(strip=True))
    region = next((tag for tag in page.select(_MAIN) if len(tag.get_text(strip=True)) * 2 > total), page)
    body = _tidy(_render(region, base_url))
    if len(body) < _SHORT and scripted:
        body = f"{body}\n\n{JAVASCRIPT_ONLY}".strip()
    if not body:
        return ""
    return f"Title: {title}\n\n{body}" if title else body


def _render(node: Tag, base: str) -> str:
    parts = []
    for child in node.children:
        if isinstance(child, Tag):
            parts.append(_tag(child, base))
        elif isinstance(child, NavigableString) and not isinstance(child, PreformattedString):
            parts.append(re.sub(r"[\s\x00]+", " ", child))  # comments and doctypes are PreformattedStrings
    return "".join(parts)


def _tag(tag: Tag, base: str) -> str:
    name = tag.name
    if name in _HEADINGS:
        text = _one_line(_render(tag, base))
        return f"\n\n{'#' * int(name[1])} {text}\n\n" if text else ""
    if name == "a":
        text = _one_line(re.sub(r"\n#+ ", "\n", _render(tag, base)))  # a card link wrapping a heading
        href = str(tag.get("href") or "").strip()
        if not href or href.lower().startswith("javascript:"):
            return text
        return f"[{text}]({urljoin(base, href)})" if text else ""
    if name == "img":
        alt = _one_line(str(tag.get("alt") or ""))
        return f"![{alt}]" if alt else ""
    if name == "br":
        return "\n"
    if name == "hr":
        return "\n\n---\n\n"
    if name == "pre":
        return f"\n\n```\n{tag.get_text().strip(chr(10))}\n```\n\n"
    if name == "code":
        return f"`{tag.get_text()}`"
    if name in {"ul", "ol"}:
        return _list(tag, base)
    if name == "table" and not tag.find("table"):  # a table inside a table is page layout, not data
        return _table(tag, base)
    if name == "blockquote":
        return "\n\n" + "\n".join(f"> {line}" for line in _render(tag, base).strip().split("\n")) + "\n\n"
    if name in _BLOCKS:
        return f"\n\n{_render(tag, base)}\n\n"
    return _render(tag, base)


def _list(tag: Tag, base: str) -> str:
    items = tag.find_all("li", recursive=False)
    if not items:
        return f"\n\n{_render(tag, base)}\n\n"
    lines = []
    for number, item in enumerate(items, start=1):
        first, *rest = _render(item, base).strip().split("\n")
        lines.append(f"{f'{number}.' if tag.name == 'ol' else '-'} {first}")
        lines.extend(_INDENT + line for line in rest if line.strip())
    return "\n\n" + "\n".join(lines) + "\n\n"


def _table(tag: Tag, base: str) -> str:
    rows = []
    for row in tag.find_all("tr"):
        cells = [_one_line(_render(cell, base)).replace("|", "\\|") for cell in row.find_all(["th", "td"], recursive=False)]
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
            if len(rows) == 1:
                rows.append("|" + " --- |" * len(cells))
    caption = _one_line(_render(tag.caption, base)) if tag.caption else ""
    return "\n\n" + "\n".join(([caption, ""] if caption else []) + rows) + "\n\n"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _tidy(text: str) -> str:
    """Trim every line, keep code blocks and list indentation as written, and leave one blank line between blocks."""
    lines, fenced = [], False
    for line in text.split("\n"):
        body = line.lstrip(" \t" + _INDENT)
        if body.startswith("```"):
            fenced = not fenced
        elif fenced:
            lines.append(line.replace(_INDENT, "  ").rstrip())
            continue
        indent = "  " * line[: len(line) - len(body)].count(_INDENT)
        lines.append(indent + re.sub(r"[ \t]{2,}", " ", body).replace(_INDENT, "  ").rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
