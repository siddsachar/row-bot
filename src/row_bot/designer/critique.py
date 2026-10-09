"""Heuristic critique and safe repair helpers for Designer pages."""

from __future__ import annotations

import math
import re
from collections import Counter

from bs4 import BeautifulSoup, Tag

from row_bot.designer.html_ops import build_selector_hint, ensure_element_identifier

_TEXT_TAGS = ("h1", "h2", "h3", "p", "li", "blockquote", "button", "a", "span")
_CONTAINER_TAGS = ("section", "article", "div")
_ALL_CATEGORIES = {"hierarchy", "overflow", "contrast", "readability", "spacing"}
_HEX_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
_RGB_RE = re.compile(r"rgba?\(([^)]+)\)")
_VAR_RE = re.compile(r"var\((--[A-Za-z0-9_-]+)\)")
_ROOT_VARS_RE = re.compile(r":root\s*\{([^}]*)\}", re.IGNORECASE | re.DOTALL)
_CSS_VAR_DECL_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+)")


class _Styles:
    """What each element sets: the page's own plain ``<style>`` rules, then its
    inline style (which wins).

    Rules inside ``@media``/``@font-face`` and selectors that cannot be matched
    here (``::after``, ``:hover``) are skipped; later rules win over earlier
    ones without weighing specificity.
    """

    def __init__(self, soup: BeautifulSoup) -> None:
        self._sheet: dict[int, dict[str, str]] = {}
        for style in soup.find_all("style"):
            for selectors, body in _css_rules(style.get_text()):
                declarations = _parse_style(body)
                if not declarations:
                    continue
                for selector in selectors.split(","):
                    try:
                        matched = soup.select(selector.strip()) if selector.strip() else []
                    except Exception:
                        continue
                    for tag in matched:
                        self._sheet.setdefault(id(tag), {}).update(declarations)

    def of(self, tag: Tag) -> dict[str, str]:
        return {**self._sheet.get(id(tag), {}), **_parse_style(tag.get("style", ""))}

    def inherited(self, tag: Tag, key: str) -> str | None:
        """The value of an inherited property such as line-height."""
        current: Tag | None = tag
        while isinstance(current, Tag):
            value = self.of(current).get(key)
            if value:
                return value
            current = current.parent
        return None


def _css_rules(css: str) -> list[tuple[str, str]]:
    """``(selectors, declarations)`` for each plain rule of a stylesheet."""
    css = re.sub(r"/\*.*?\*/", "", css or "", flags=re.DOTALL)
    rules: list[tuple[str, str]] = []
    index = 0
    while (brace := css.find("{", index)) >= 0:
        depth, end = 1, brace + 1
        while end < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[end], 0)
            end += 1
        selector = css[index:brace].rsplit(";", 1)[-1].strip()
        body = css[brace + 1:end - 1]
        if selector and not selector.startswith("@") and "{" not in body:
            rules.append((selector, body))
        index = end
    return rules


_SPACING_FUNCTION_RE = re.compile(r"\b(?:clamp|calc|var|min|max)\(")
_LENGTH_RE = re.compile(r"-?\d*\.?\d+")


def _spaces(value: str | None) -> bool:
    """A spacing value that leaves room: not empty, ``0`` or ``auto``."""
    if not value:
        return False
    if _SPACING_FUNCTION_RE.search(value):
        return True
    return any(float(number) > 0 for number in _LENGTH_RE.findall(value))


def critique_page_html(page_html: str, canvas_width: int, canvas_height: int) -> dict:
    """Return a structured critique report for one page of HTML."""

    soup = BeautifulSoup(page_html or "", "html.parser")
    root = soup.body or soup
    body = soup.body if isinstance(soup.body, Tag) else root
    variables = _extract_css_variables(page_html)
    body_style = _parse_style(body.get("style", "") if isinstance(body, Tag) else "")
    body_color_raw = body_style.get("color") or "var(--text)"
    body_bg_raw = body_style.get("background-color") or body_style.get("background") or "var(--bg)"
    styles = _Styles(soup)

    findings: list[dict] = []
    _add_hierarchy_findings(root, findings)
    _add_overflow_findings(root, canvas_width, canvas_height, findings)
    _add_contrast_findings(root, variables, body_color_raw, body_bg_raw, findings)
    _add_readability_findings(root, styles, canvas_width, findings)
    _add_spacing_findings(root, styles, findings)

    findings = findings[:12]
    severities = Counter(finding["severity"] for finding in findings)
    score = max(0, 100 - severities.get("high", 0) * 18 - severities.get("medium", 0) * 10 - severities.get("low", 0) * 4)
    counts = Counter(finding["category"] for finding in findings)
    summary = "No obvious layout issues detected." if not findings else (
        f"{len(findings)} issue(s): " + ", ".join(f"{counts[key]} {key}" for key in sorted(counts))
    )

    return {
        "score": score,
        "summary": summary,
        "category_counts": dict(counts),
        "word_count": _word_count(root),
        "findings": findings,
    }


def apply_page_repairs(
    page_html: str,
    canvas_width: int,
    canvas_height: int,
    categories: list[str] | str | None = None,
) -> tuple[str, list[dict]]:
    """Apply safe deterministic repairs for the selected critique categories."""

    selected = _normalize_categories(categories)
    soup = BeautifulSoup(page_html or "", "html.parser")
    root = soup.body or soup
    body = soup.body if isinstance(soup.body, Tag) else root
    variables = _extract_css_variables(page_html)
    body_style = _parse_style(body.get("style", "") if isinstance(body, Tag) else "")
    body_bg_raw = body_style.get("background-color") or body_style.get("background") or "var(--bg)"

    styles = _Styles(soup)
    changes: list[dict] = []
    if "hierarchy" in selected:
        _repair_hierarchy(root, changes)
    if "readability" in selected:
        _repair_readability(root, styles, canvas_width, changes)
    if "spacing" in selected:
        _repair_spacing(root, styles, changes)
    if "contrast" in selected:
        _repair_contrast(root, variables, body_bg_raw, changes)
    if "overflow" in selected:
        _repair_overflow(root, canvas_width, canvas_height, changes)

    if not changes:
        return page_html, []
    return str(soup), changes


def _normalize_categories(categories: list[str] | str | None) -> set[str]:
    if categories is None:
        return set(_ALL_CATEGORIES)
    if isinstance(categories, str):
        raw = [token.strip().lower() for token in categories.split(",") if token.strip()]
    else:
        raw = [str(token).strip().lower() for token in categories if str(token).strip()]
    selected = set(raw) if raw else set(_ALL_CATEGORIES)
    invalid = selected - _ALL_CATEGORIES
    if invalid:
        valid = ", ".join(sorted(_ALL_CATEGORIES))
        raise ValueError(f"Unknown critique categories: {', '.join(sorted(invalid))}. Valid: {valid}.")
    return selected


def _extract_css_variables(page_html: str) -> dict[str, str]:
    variables: dict[str, str] = {}
    for match in _ROOT_VARS_RE.finditer(page_html or ""):
        for key, value in _CSS_VAR_DECL_RE.findall(match.group(1)):
            variables[key.strip()] = value.strip()
    return variables


def _parse_style(style_attr: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for declaration in style_attr.split(";"):
        declaration = declaration.strip()
        if not declaration or ":" not in declaration:
            continue
        key, value = declaration.split(":", 1)
        parsed[key.strip().lower()] = value.strip()
    return parsed


def _serialize_style(style_map: dict[str, str]) -> str:
    return "; ".join(f"{key}: {value}" for key, value in style_map.items() if value)


def _resolve_color(raw: str | None, variables: dict[str, str]) -> tuple[float, float, float] | None:
    if not raw:
        return None
    value = raw.strip().lower()
    var_match = _VAR_RE.fullmatch(value)
    if var_match:
        value = variables.get(var_match.group(1), "").strip().lower()
    if not value or any(token in value for token in ("linear-gradient", "radial-gradient", "color-mix", "transparent", "inherit", "currentcolor")):
        return None
    if value == "white":
        return (1.0, 1.0, 1.0)
    if value == "black":
        return (0.0, 0.0, 0.0)

    hex_match = _HEX_RE.search(value)
    if hex_match:
        hex_value = hex_match.group(1)
        if len(hex_value) == 3:
            hex_value = "".join(ch * 2 for ch in hex_value)
        return tuple(int(hex_value[i:i + 2], 16) / 255 for i in (0, 2, 4))

    rgb_match = _RGB_RE.search(value)
    if rgb_match:
        parts = [part.strip() for part in rgb_match.group(1).split(",")]
        if len(parts) >= 3:
            try:
                # A see-through color shows what is behind it: it is no
                # background of its own, so callers look past it.
                if len(parts) >= 4:
                    alpha = parts[3]
                    opacity = float(alpha[:-1]) / 100 if alpha.endswith("%") else float(alpha)
                    if opacity < 1:
                        return None
                return tuple(max(0.0, min(255.0, float(parts[i]))) / 255 for i in range(3))
            except ValueError:
                return None
    return None


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def _channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (_channel(channel) for channel in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _font_px(value: str | None) -> float | None:
    """An inline font size in pixels (px, rem or em), when it is one."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(px|rem|em)\s*", value or "")
    if not match:
        return None
    size = float(match.group(1))
    return size if match.group(2) == "px" else size * 16


def _contrast_minimum(tag: Tag) -> float:
    """WCAG AA: 3:1 for large text (24px, or 18.66px bold), else 4.5:1.

    Size and weight are inherited, so the nearest element that sets each wins.
    """
    size: float | None = None
    weight: str | None = None
    current: Tag | None = tag
    while isinstance(current, Tag) and (size is None or weight is None):
        style = _parse_style(current.get("style", ""))
        if size is None:
            # Browsers draw h1 and h2 at 32px and 24px by default.
            size = _font_px(style.get("font-size")) or {"h1": 32.0, "h2": 24.0}.get(current.name)
        if weight is None:
            weight = style.get("font-weight") or (
                "bold" if current.name in {"h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"} else None)
        current = current.parent
    bold = weight == "bold" or (weight or "").isdigit() and int(weight or 0) >= 700
    large = size is not None and (size >= 24 or bold and size >= 18.66)
    return 3.0 if large else 4.5


def _contrast_ratio(foreground: tuple[float, float, float], background: tuple[float, float, float]) -> float:
    l1 = _relative_luminance(foreground)
    l2 = _relative_luminance(background)
    high, low = max(l1, l2), min(l1, l2)
    return (high + 0.05) / (low + 0.05)


def _word_count(root: Tag | BeautifulSoup) -> int:
    text = " ".join(tag.get_text(" ", strip=True) for tag in root.find_all(_TEXT_TAGS))
    return len([token for token in text.split() if token])


def _text_excerpt(tag: Tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()[:120]


def _numeric_px(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)px", value)
    if match:
        return float(match.group(1))
    return None


def _line_height_value(value: str | None) -> float | None:
    if not value:
        return None
    try:
        if value.endswith("px"):
            return float(value[:-2].strip())
        return float(value)
    except ValueError:
        return None


def _add_finding(findings: list[dict], category: str, severity: str, message: str,
                 suggested_fix: str, tag: Tag | None = None,
                 *, auto_fixable: bool = True) -> None:
    payload = {
        "category": category,
        "severity": severity,
        "message": message,
        "suggested_fix": suggested_fix,
        "auto_fixable": auto_fixable,
    }
    if tag is not None:
        payload["element_ref"] = ensure_element_identifier(tag)
        payload["selector_hint"] = build_selector_hint(tag, tag.find_parent("body") or tag)
        payload["excerpt"] = _text_excerpt(tag)
    findings.append(payload)


def _add_hierarchy_findings(root: Tag | BeautifulSoup, findings: list[dict]) -> None:
    headings = root.find_all(["h1", "h2", "h3"])
    if not headings:
        # A blank starter page has nothing to organize yet.
        if _word_count(root) < 12:
            return
        _add_finding(
            findings,
            "hierarchy",
            "high",
            "The page has no visible heading tags, so the information hierarchy may feel flat.",
            "Introduce at least one primary heading and clear subheads.",
            auto_fixable=False,
        )
        return

    h1s = root.find_all("h1")
    if not h1s:
        _add_finding(
            findings,
            "hierarchy",
            "medium",
            "The page starts with secondary headings but no primary heading.",
            "Promote the lead headline into an h1-level entry point.",
            headings[0],
        )
    elif len(h1s) > 1:
        _add_finding(
            findings,
            "hierarchy",
            "low",
            "Multiple h1 headings compete for the top-level emphasis on this page.",
            "Keep one dominant h1 and use h2 or h3 for supporting sections.",
            h1s[1],
        )

    first_heading = headings[0]
    if len(_text_excerpt(first_heading)) > 90:
        _add_finding(
            findings,
            "hierarchy",
            "low",
            "The lead headline is very long, which can blunt the main visual signal.",
            "Shorten the headline or split the supporting detail into body copy.",
            first_heading,
        )


def _add_overflow_findings(root: Tag | BeautifulSoup, canvas_width: int, canvas_height: int, findings: list[dict]) -> None:
    base_area = 1920 * 1080
    scale = math.sqrt(max(0.35, (canvas_width * canvas_height) / base_area))
    word_budget = int(220 * scale)
    total_words = _word_count(root)
    structural_blocks = len(root.find_all(["section", "article"]))
    # Also count top-level card-like blocks inside a body/main/section so we
    # catch fixed-slide pages that stack many <div class="card"> blocks
    # without using <section>. Limit depth to 3 to avoid over-counting
    # deeply nested decorative wrappers.
    card_like = 0
    body = root.find("body") or root
    for child in body.descendants:
        if not hasattr(child, "name") or child.name != "div":
            continue
        cls = " ".join(child.get("class") or []).lower()
        style = (child.get("style") or "").lower()
        looks_card = (
            any(k in cls for k in ("card", "panel", "tile", "metric", "stat", "chip", "pill", "callout"))
            or ("background" in style and ("border-radius" in style or "padding" in style))
        )
        # A card holds something to read; an empty bar or placeholder line
        # (a progress bar, a wireframe skeleton) is not a section.
        if looks_card and child.get_text(strip=True):
            card_like += 1
    # A tall page (a landing page scrolls) has room for proportionally more.
    room = max(1, canvas_height // 1080)
    if total_words > word_budget:
        _add_finding(
            findings,
            "overflow",
            "medium",
            f"The page carries about {total_words} words, which risks overflow for a {canvas_width}x{canvas_height} canvas.",
            "Condense copy, tighten spacing, or split the content across more pages.",
        )
    elif structural_blocks >= 5 * room or card_like >= 7 * room:
        _add_finding(
            findings,
            "overflow",
            "low",
            "The page stacks many structural sections, which may create vertical pressure.",
            "Merge lower-priority sections or move one section to another page.",
        )


def _add_contrast_findings(root: Tag | BeautifulSoup, variables: dict[str, str],
                           body_color_raw: str, body_bg_raw: str, findings: list[dict]) -> None:
    body_color = _resolve_color(body_color_raw, variables)
    body_bg = _resolve_color(body_bg_raw, variables)
    seen = 0
    for tag in root.find_all(_TEXT_TAGS):
        if seen >= 6:
            break
        excerpt = _text_excerpt(tag)
        if not excerpt:
            continue
        style = _parse_style(tag.get("style", ""))
        foreground = _resolve_color(style.get("color") or body_color_raw, variables) or body_color
        background = _resolve_color(style.get("background-color") or style.get("background") or body_bg_raw, variables) or body_bg
        if foreground is None or background is None:
            continue
        ratio = _contrast_ratio(foreground, background)
        if ratio < _contrast_minimum(tag):
            severity = "high" if ratio < 3 else "medium"
            _add_finding(
                findings,
                "contrast",
                severity,
                f"Text contrast is estimated around {ratio:.2f}:1, which may be hard to read.",
                "Raise contrast by darkening the text or simplifying the background behind it.",
                tag,
            )
            seen += 1


def _needs_readability(tag: Tag, styles: _Styles, canvas_width: int) -> bool:
    """Long copy without a measure, a comfortable line height or size.

    A phone-width page already keeps its lines short; line height is
    inherited, so a page-wide ``body { line-height: 1.5 }`` counts.
    """
    if len(_text_excerpt(tag)) < 80:
        return False
    style = styles.of(tag)
    has_max_width = canvas_width <= 640 or any(key in style for key in ("max-width", "width"))
    line_height = _line_height_value(styles.inherited(tag, "line-height"))
    font_size = _numeric_px(style.get("font-size"))
    return (not has_max_width or line_height is None or line_height < 1.4
            or (font_size is not None and font_size < 14))


def _add_readability_findings(root: Tag | BeautifulSoup, styles: _Styles, canvas_width: int,
                              findings: list[dict]) -> None:
    seen = 0
    for tag in root.find_all(["p", "li", "blockquote"]):
        if seen >= 4:
            break
        if _needs_readability(tag, styles, canvas_width):
            _add_finding(
                findings,
                "readability",
                "medium",
                "Long-form copy lacks one or more readability guards such as max width, comfortable line height, or sufficient size.",
                "Constrain measure to roughly 60-70 characters and keep body copy at comfortable size and line height.",
                tag,
            )
            seen += 1


def _spacing_needs(tag: Tag, styles: _Styles) -> tuple[bool, bool]:
    """(needs a gap, needs padding) for a container of three or more blocks.

    Its blocks are spaced by a gap, or by a margin between each pair of
    neighbours. A flex or grid layout set on the element itself needs that;
    a section or article needs padding when it has a background of its own
    or nothing else spaces its blocks. Values from the page's own styles
    count; ``0`` and ``auto`` do not.
    """
    children = [child for child in tag.children if isinstance(child, Tag)]
    if len(children) < 3:
        return False, False
    style = styles.of(tag)
    has_gap = any(_spaces(style.get(key)) for key in ("gap", "row-gap", "column-gap"))
    margins = [any(_spaces(value) for key, value in styles.of(child).items() if key.startswith("margin"))
               for child in children]
    spaced = has_gap or all(left or right for left, right in zip(margins, margins[1:]))
    has_padding = tag.name == "body" or any(
        _spaces(value) for key, value in style.items() if key.startswith("padding"))
    filled = any(key in {"background", "background-color"} and value.strip().lower() not in {"none", "transparent"}
                 for key, value in style.items())
    layout = _parse_style(tag.get("style", "")).get("display", "")
    return (layout in {"flex", "grid"} and not spaced,
            tag.name in {"section", "article"} and not has_padding and (filled or not spaced))


def _add_spacing_findings(root: Tag | BeautifulSoup, styles: _Styles, findings: list[dict]) -> None:
    seen = 0
    for tag in root.find_all(_CONTAINER_TAGS):
        if seen >= 4:
            break
        if any(_spacing_needs(tag, styles)):
            _add_finding(
                findings,
                "spacing",
                "low",
                "A multi-element container is missing explicit spacing controls.",
                "Add gap and padding so adjacent blocks do not visually collapse together.",
                tag,
            )
            seen += 1


def _record_change(changes: list[dict], category: str, tag: Tag, description: str) -> None:
    changes.append({
        "category": category,
        "element_ref": ensure_element_identifier(tag),
        "selector_hint": build_selector_hint(tag, tag.find_parent("body") or tag),
        "description": description,
    })


def _update_style(tag: Tag, **updates: str) -> bool:
    style = _parse_style(tag.get("style", ""))
    changed = False
    for key, value in updates.items():
        if value and style.get(key) != value:
            style[key] = value
            changed = True
    if changed:
        tag["style"] = _serialize_style(style)
    return changed


def _repair_hierarchy(root: Tag | BeautifulSoup, changes: list[dict]) -> None:
    headings = root.find_all(["h1", "h2", "h3"])
    if not headings:
        return
    first_heading = headings[0]
    style = _parse_style(first_heading.get("style", ""))
    size = _numeric_px(style.get("font-size"))
    if size is None or size < 40:
        if _update_style(first_heading, **{
            "font-size": "44px",
            "line-height": "1.05",
            "font-weight": "700",
            "letter-spacing": "-0.03em",
        }):
            _record_change(changes, "hierarchy", first_heading, "Strengthened the lead heading.")


def _repair_readability(root: Tag | BeautifulSoup, styles: _Styles, canvas_width: int,
                        changes: list[dict]) -> None:
    for tag in root.find_all(["p", "li", "blockquote"]):
        if not _needs_readability(tag, styles, canvas_width):
            continue
        style = styles.of(tag)
        font_size = _numeric_px(style.get("font-size"))
        line_height = _line_height_value(styles.inherited(tag, "line-height"))
        updated = _update_style(tag, **{
            "max-width": ("" if canvas_width <= 640 or "width" in style or "max-width" in style
                          else "62ch"),
            "line-height": "1.55" if (line_height or 0) < 1.45 else "",
            "font-size": "15px" if font_size is not None and font_size < 14 else "",
        })
        if updated:
            _record_change(changes, "readability", tag, "Improved text measure and line spacing.")


def _repair_spacing(root: Tag | BeautifulSoup, styles: _Styles, changes: list[dict]) -> None:
    for tag in root.find_all(_CONTAINER_TAGS):
        needs_gap, needs_padding = _spacing_needs(tag, styles)
        updates = {}
        if needs_gap:
            updates["gap"] = "16px"
        if needs_padding:
            updates["padding"] = "24px"
        if updates and _update_style(tag, **updates):
            _record_change(changes, "spacing", tag, "Added padding or gap to a dense container.")


def _repair_contrast(root: Tag | BeautifulSoup, variables: dict[str, str], body_bg_raw: str, changes: list[dict]) -> None:
    body_bg = _resolve_color(body_bg_raw, variables)
    for tag in root.find_all(_TEXT_TAGS):
        excerpt = _text_excerpt(tag)
        if not excerpt:
            continue
        style = _parse_style(tag.get("style", ""))
        foreground = _resolve_color(style.get("color") or "var(--text)", variables)
        background = _resolve_color(style.get("background-color") or style.get("background") or body_bg_raw, variables) or body_bg
        if foreground is None or background is None:
            continue
        if _contrast_ratio(foreground, background) >= 4.5:
            continue
        ink = "#F8FAFC" if _relative_luminance(background) < 0.35 else "#0F172A"
        if _update_style(tag, color=ink):
            _record_change(changes, "contrast", tag, "Raised text contrast against its background.")


def _repair_overflow(root: Tag | BeautifulSoup, canvas_width: int, canvas_height: int, changes: list[dict]) -> None:
    for heading in root.find_all(["h1", "h2", "h3"]):
        style = _parse_style(heading.get("style", ""))
        size = _numeric_px(style.get("font-size"))
        if size is not None and size > 60:
            if _update_style(heading, **{"font-size": f"{max(34, int(size * 0.82))}px"}):
                _record_change(changes, "overflow", heading, "Reduced an oversized heading to ease vertical pressure.")

    for tag in root.find_all(_CONTAINER_TAGS):
        style = _parse_style(tag.get("style", ""))
        updates = {}
        for key in ("padding", "gap", "row-gap", "column-gap", "margin-top", "margin-bottom"):
            size = _numeric_px(style.get(key))
            if size is not None and size > 32:
                updates[key] = f"{max(16, int(size * 0.75))}px"
        if updates and _update_style(tag, **updates):
            _record_change(changes, "overflow", tag, "Reduced oversized spacing to recover layout room.")