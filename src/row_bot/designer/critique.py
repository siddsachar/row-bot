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


def critique_page_html(page_html: str, canvas_width: int, canvas_height: int) -> dict:
    """Return a structured critique report for one page of HTML."""

    soup = BeautifulSoup(page_html or "", "html.parser")
    root = soup.body or soup
    body = soup.body if isinstance(soup.body, Tag) else root
    variables = _extract_css_variables(page_html)
    body_style = _parse_style(body.get("style", "") if isinstance(body, Tag) else "")
    body_color_raw = body_style.get("color") or "var(--text)"
    body_bg_raw = body_style.get("background-color") or body_style.get("background") or "var(--bg)"

    findings: list[dict] = []
    _add_hierarchy_findings(root, findings)
    _add_overflow_findings(root, canvas_width, canvas_height, findings)
    _add_contrast_findings(root, variables, body_color_raw, body_bg_raw, findings)
    _add_readability_findings(root, findings)
    _add_spacing_findings(root, findings)

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

    changes: list[dict] = []
    if "hierarchy" in selected:
        _repair_hierarchy(root, changes)
    if "readability" in selected:
        _repair_readability(root, changes)
    if "spacing" in selected:
        _repair_spacing(root, changes)
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
                return tuple(max(0.0, min(255.0, float(parts[i]))) / 255 for i in range(3))
            except ValueError:
                return None
    return None


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def _channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (_channel(channel) for channel in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


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
        if looks_card:
            card_like += 1
    if total_words > word_budget:
        _add_finding(
            findings,
            "overflow",
            "medium",
            f"The page carries about {total_words} words, which risks overflow for a {canvas_width}x{canvas_height} canvas.",
            "Condense copy, tighten spacing, or split the content across more pages.",
        )
    elif structural_blocks >= 5 or card_like >= 7:
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
        if ratio < 4.5:
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


def _add_readability_findings(root: Tag | BeautifulSoup, findings: list[dict]) -> None:
    seen = 0
    for tag in root.find_all(["p", "li", "blockquote"]):
        if seen >= 4:
            break
        excerpt = _text_excerpt(tag)
        if len(excerpt) < 80:
            continue
        style = _parse_style(tag.get("style", ""))
        has_max_width = any(key in style for key in ("max-width", "width"))
        line_height = _line_height_value(style.get("line-height"))
        font_size = _numeric_px(style.get("font-size"))
        if not has_max_width or line_height is None or line_height < 1.4 or (font_size is not None and font_size < 14):
            _add_finding(
                findings,
                "readability",
                "medium",
                "Long-form copy lacks one or more readability guards such as max width, comfortable line height, or sufficient size.",
                "Constrain measure to roughly 60-70 characters and keep body copy at comfortable size and line height.",
                tag,
            )
            seen += 1


def _add_spacing_findings(root: Tag | BeautifulSoup, findings: list[dict]) -> None:
    seen = 0
    for tag in root.find_all(_CONTAINER_TAGS):
        if seen >= 4:
            break
        children = [child for child in tag.children if isinstance(child, Tag)]
        if len(children) < 3:
            continue
        style = _parse_style(tag.get("style", ""))
        display = style.get("display", "")
        has_gap = any(key in style for key in ("gap", "row-gap", "column-gap"))
        has_padding = "padding" in style or tag.name == "body"
        if (display in {"flex", "grid"} and not has_gap) or (tag.name in {"section", "article"} and not has_padding):
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


def _repair_readability(root: Tag | BeautifulSoup, changes: list[dict]) -> None:
    for tag in root.find_all(["p", "li", "blockquote"]):
        if len(_text_excerpt(tag)) < 80:
            continue
        style = _parse_style(tag.get("style", ""))
        font_size = _numeric_px(style.get("font-size"))
        updated = _update_style(tag, **{
            "max-width": style.get("max-width", "62ch") or "62ch",
            "line-height": "1.55" if (_line_height_value(style.get("line-height")) or 0) < 1.45 else style.get("line-height", "1.55"),
            "font-size": "15px" if font_size is not None and font_size < 14 else style.get("font-size", ""),
        })
        if updated:
            _record_change(changes, "readability", tag, "Improved text measure and line spacing.")


def _repair_spacing(root: Tag | BeautifulSoup, changes: list[dict]) -> None:
    for tag in root.find_all(_CONTAINER_TAGS):
        children = [child for child in tag.children if isinstance(child, Tag)]
        if len(children) < 3:
            continue
        style = _parse_style(tag.get("style", ""))
        display = style.get("display", "")
        updates = {}
        if display in {"flex", "grid"} and not any(key in style for key in ("gap", "row-gap", "column-gap")):
            updates["gap"] = "16px"
        if tag.name in {"section", "article"} and "padding" not in style:
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