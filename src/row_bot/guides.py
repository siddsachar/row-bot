"""Row-Bot's own guides, read offline from the copy that ships with this version.

Every install carries the guide sources (``docs-site/docs``, the Markdown the
public docs site is built from), so Row-Bot can answer "how do I…" about the
version that is running, with no network. Pages are split into sections by
their headings, each with the published route and the anchor the docs site
gives it, and ranked with BM25 (titles and headings weigh more than body text).

Standard library only: the docs scripts import the cleaner from here without
installing the app.
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

GUIDES_ROOT = "/docs"
_SUFFIXES = (".md", ".mdx")
_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_COMPONENT_LINE = re.compile(r"^\s*<[A-Za-z][^>]*/>\s*$")
_COMPONENT_OPEN = re.compile(r"^\s*<([A-Z][A-Za-z0-9]*)\b[^>]*>\s*$")
_ADMONITION = re.compile(r"^:::(\w+)\s*(.*)$")
_COMMENTS = re.compile(r"\{/\*.*?\*/\}|<!--.*?-->", re.DOTALL)
_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)
# Short words that say nothing about which guide answers a question.
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it its me my of on or so that the this to "
    "what when where which who why will with you your".split()
)
_K1 = 1.2
_B = 0.75
# How much a word in the section's heading path or its page's title counts against one in the body.
_HEADING_WEIGHT = 3
_TITLE_WEIGHT = 2


class GuidesUnavailable(RuntimeError):
    """This install has no guides folder (a source checkout without ``docs-site``, say)."""


@dataclass(frozen=True)
class GuideSection:
    """One part of a guide page: its intro (no anchor) or one ``##``/``###`` section."""

    route: str
    anchor: str
    page_title: str
    heading: str
    text: str

    @property
    def link(self) -> str:
        return f"{self.route}#{self.anchor}" if self.anchor else self.route

    @property
    def title(self) -> str:
        """``Page › Section`` (the page title alone for its intro)."""
        return self.page_title if not self.anchor else f"{self.page_title} › {self.heading}"


@dataclass(frozen=True)
class GuidePage:
    route: str
    title: str
    description: str
    sections: tuple[GuideSection, ...]

    @property
    def group(self) -> str:
        """The first part of its route ("settings", "chat"…), "" for the guides' home page."""
        parts = self.route.removeprefix(GUIDES_ROOT).strip("/").split("/")
        return parts[0] if parts and parts[0] else ""

    @property
    def text(self) -> str:
        """The whole page as Markdown, sections under their headings."""
        lines = []
        for section in self.sections:
            if section.anchor:
                lines.append(f"## {section.heading}")
            if section.text:
                lines.append(section.text)
        return "\n\n".join(lines)


# ── Cleaning one page ────────────────────────────────────────────────────────


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    """``(front matter, body)``; front matter values are plain strings."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    data: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        key, sep, value = line.partition(":")
        if sep:
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data, text[end + 4:].lstrip("\n")


def clean_markdown(body: str) -> str:
    """A guide page's Markdown without the site's code: imports, components, comments.

    Lines, lists, tables, code blocks and Unicode (the › in "Settings › System")
    stay as written. Admonitions become a bold lead line.
    """
    lines: list[str] = []
    in_fence = False
    open_component = ""
    for line in _COMMENTS.sub("", body).splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            lines.append(line)
            continue
        if in_fence:
            lines.append(line)
            continue
        if open_component:
            if line.strip() == f"</{open_component}>":
                open_component = ""
            continue
        stripped = line.strip()
        if stripped.startswith(("import ", "export ")) or _COMPONENT_LINE.match(line):
            continue
        component = _COMPONENT_OPEN.match(line)
        if component:
            open_component = component.group(1)
            continue
        admonition = _ADMONITION.match(stripped)
        if admonition:
            kind, label = admonition.groups()
            lines.append(f"**{kind.capitalize()}: {label}**" if label else f"**{kind.capitalize()}**")
            continue
        if stripped == ":::":
            continue
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def heading_text(markdown: str) -> str:
    """A heading as the docs site shows it: without emphasis, code marks or link targets."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", markdown)
    text = re.sub(r"[*_`]", "", text)
    return text.strip()


def anchor_for(heading: str, taken: dict[str, int]) -> str:
    """The anchor the docs site gives a heading (GitHub's slugs), unique within its page."""
    slug = re.sub(r"[^\w\- ]", "", heading.lower(), flags=re.UNICODE).replace(" ", "-")
    count = taken.get(slug, 0)
    taken[slug] = count + 1
    return slug if count == 0 else f"{slug}-{count}"


def route_for(path: Path, root: Path) -> str:
    """The published route of a guide file, as the docs site serves it."""
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts == ["index"]:
        return f"{GUIDES_ROOT}/"
    if parts[-1] == "index":
        return f"{GUIDES_ROOT}/" + "/".join(parts[:-1]) + "/"
    return f"{GUIDES_ROOT}/" + "/".join(parts)


def parse_page(text: str, route: str) -> GuidePage:
    """One guide page split into its intro and sections."""
    meta, body = split_front_matter(text)
    cleaned = clean_markdown(body)
    title = meta.get("title") or route.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
    sections: list[GuideSection] = []
    taken: dict[str, int] = {}
    heading, anchor, chunk = "", "", []

    def close() -> None:
        content = "\n".join(chunk).strip()
        if content or anchor:
            sections.append(GuideSection(route, anchor, title, heading or title, content))

    in_fence = False
    for line in cleaned.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            close()
            heading = heading_text(match.group(2))
            anchor = anchor_for(heading, taken)
            chunk = []
        elif in_fence or heading or not line.startswith("# ") or "\n".join(chunk).strip():
            chunk.append(line)
        # Else it is the page's own "# Title", which repeats its title: left out.
    close()
    return GuidePage(route, title, meta.get("description", ""), tuple(sections))


# ── The index ────────────────────────────────────────────────────────────────


def _words(text: str) -> list[str]:
    return [word for word in (match.group(0).lower() for match in _WORD.finditer(text))
            if word not in _STOPWORDS]


@dataclass(frozen=True)
class _Index:
    pages: tuple[GuidePage, ...]
    sections: tuple[GuideSection, ...]
    weights: tuple[Counter, ...]
    lengths: tuple[int, ...]
    document_frequency: Counter

    @property
    def average_length(self) -> float:
        return (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0


def _build(root: Path) -> _Index:
    pages = []
    for path in sorted(item for item in root.rglob("*") if item.suffix in _SUFFIXES and item.is_file()):
        pages.append(parse_page(path.read_text(encoding="utf-8", errors="replace"), route_for(path, root)))
    sections = tuple(section for page in pages for section in page.sections)
    weights = []
    for section in sections:
        terms = Counter(_words(section.text))
        for word in _words(section.heading if section.anchor else ""):
            terms[word] += _HEADING_WEIGHT
        for word in _words(section.page_title):
            terms[word] += _TITLE_WEIGHT
        weights.append(terms)
    frequency: Counter = Counter()
    for terms in weights:
        frequency.update(terms.keys())
    return _Index(tuple(pages), sections, tuple(weights), tuple(sum(terms.values()) for terms in weights), frequency)


_lock = threading.Lock()
_cached: tuple[Path, float, int, _Index] | None = None


def guides_dir() -> Path:
    """The guides folder of this install (``docs-site/docs`` beside the app)."""
    from row_bot.runtime_paths import guides_dir as installed_guides_dir

    return installed_guides_dir()


def _index(root: Path | None = None) -> _Index:
    global _cached
    folder = guides_dir() if root is None else root
    if not folder.is_dir():
        raise GuidesUnavailable("This install has no guides. They're at row-bot.ai/docs.")
    files = [item for item in folder.rglob("*") if item.suffix in _SUFFIXES and item.is_file()]
    newest = max((item.stat().st_mtime for item in files), default=0.0)
    with _lock:
        if _cached is not None and _cached[:3] == (folder, newest, len(files)):
            return _cached[3]
    index = _build(folder)
    with _lock:
        _cached = (folder, newest, len(files), index)
    return index


def pages(root: Path | None = None) -> tuple[GuidePage, ...]:
    """Every guide page, in route order."""
    return _index(root).pages


def search(query: str, *, limit: int = 4, root: Path | None = None) -> list[GuideSection]:
    """The sections that best answer *query*, best first (empty when nothing matches)."""
    index = _index(root)
    words = list(dict.fromkeys(_words(query)))
    if not words or not index.sections:
        return []
    total = len(index.sections)
    average = index.average_length or 1.0
    scored: list[tuple[float, int]] = []
    for position, terms in enumerate(index.weights):
        score = 0.0
        for word in words:
            weight = terms.get(word, 0)
            if not weight:
                continue
            found_in = index.document_frequency[word]
            idf = math.log(1 + (total - found_in + 0.5) / (found_in + 0.5))
            norm = _K1 * (1 - _B + _B * index.lengths[position] / average)
            score += idf * weight * (_K1 + 1) / (weight + norm)
        if score > 0:
            scored.append((score, position))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [index.sections[position] for _score, position in scored[:max(1, limit)]]


def read(link: str, *, root: Path | None = None) -> GuidePage | GuideSection | None:
    """A page (``/docs/settings/system``) or one of its sections (``…#privacy``), or None."""
    route, _sep, anchor = str(link or "").strip().partition("#")
    if not route.startswith(GUIDES_ROOT):
        return None
    wanted = route.rstrip("/")
    for page in _index(root).pages:
        if page.route.rstrip("/") != wanted:
            continue
        if not anchor:
            return page
        return next((section for section in page.sections if section.anchor == anchor), None)
    return None
