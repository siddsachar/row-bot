"""Read-only brand-lint scanner for designer pages (Phase 2.1.H).

Produces warnings (no auto-fix in 2.1) across five categories:
- ``contrast``       — WCAG AA text contrast < 4.5:1
- ``off_palette``    — hex colors outside the brand palette
- ``font``           — font-families outside the brand fonts + safe generics
- ``missing_alt``    — ``<img>`` tags with empty ``alt``
- ``logo_safe_zone`` — large elements overlapping the configured logo corner

The core ``lint_project`` / ``lint_page`` helpers are pure (no NiceGUI) so
tests can exercise them directly. The dialog renderer lives at the bottom
behind an ``open_brand_lint_dialog`` entry point that the editor calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

from bs4 import BeautifulSoup, Tag

from row_bot.designer.critique import (
    _HEX_RE,
    _resolve_color,
    _contrast_ratio,
    _parse_style,
    _extract_css_variables,
    _update_style,
    _record_change,
    _repair_contrast,
)
from row_bot.designer.html_ops import build_selector_hint, ensure_element_identifier


# ── Constants ────────────────────────────────────────────────────────────

_LINT_CATEGORIES = (
    "contrast", "off_palette", "font", "missing_alt", "logo_safe_zone",
)

# Fonts that are always acceptable alongside brand fonts. Generic
# fallbacks (serif/sans-serif/monospace/cursive/fantasy) are included so
# they don't get flagged when used as the last item in a font-family
# stack (e.g. ``font-family: Inter, sans-serif``).
_SAFE_FONTS = {
    "system-ui", "sans-serif", "serif", "monospace", "cursive", "fantasy",
    "-apple-system", "blinkmacsystemfont", "segoe ui", "inherit",
    "initial", "unset", "ui-sans-serif", "ui-serif", "ui-monospace",
    "helvetica", "arial", "times new roman", "times", "roboto",
    "'system-ui'",
}

# Colors that are always acceptable (neutral anchors).
_NEUTRAL_HEXES = {
    "#000000", "#ffffff", "#fff", "#000", "transparent",
}

# Elements considered "text" for contrast checks.
_TEXT_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "a",
              "button", "span", "blockquote")


# ── Data model ───────────────────────────────────────────────────────────

@dataclass
class LintFinding:
    category: str
    severity: str  # "low" | "medium" | "high"
    message: str
    suggested_fix: str
    page_index: int = -1
    element_ref: str = ""
    selector_hint: str = ""
    excerpt: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Color helpers ────────────────────────────────────────────────────────

def _hex6(value: str) -> str | None:
    """Normalize a hex color string to lowercase #rrggbb. Returns None on
    failure. Accepts ``#abc``, ``#aabbcc``, ``rgb(r,g,b)``, ``black``."""
    if not value:
        return None
    v = value.strip().lower()
    if v in _NEUTRAL_HEXES:
        if v in {"#fff", "#ffffff"}:
            return "#ffffff"
        if v in {"#000", "#000000"}:
            return "#000000"
        return None  # transparent, etc.
    m = _HEX_RE.search(v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return "#" + h.lower()
    rgb = _resolve_color(v, {})
    if rgb is not None:
        r, g, b = (int(round(c * 255)) for c in rgb)
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _channel_delta(a: str, b: str) -> int:
    """Max per-channel difference between two #rrggbb strings."""
    try:
        ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
        br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
        return max(abs(ar - br), abs(ag - bg), abs(ab - bb))
    except Exception:
        return 255


def _brand_hexes(brand) -> set[str]:
    """Collect normalized hex colors from a BrandConfig."""
    out: set[str] = set()
    if brand is None:
        return out
    for attr in ("primary_color", "secondary_color", "accent_color",
                 "bg_color", "text_color"):
        h = _hex6(getattr(brand, attr, "") or "")
        if h:
            out.add(h)
    return out


def _brand_fonts(brand) -> set[str]:
    """Collect lowercase brand font names."""
    out: set[str] = set()
    if brand is None:
        return out
    for attr in ("heading_font", "body_font"):
        val = (getattr(brand, attr, "") or "").strip().lower()
        if val:
            out.add(val)
    return out


# ── Core scanners (pure) ─────────────────────────────────────────────────

def _iter_text_tags(root: Tag | BeautifulSoup) -> Iterable[Tag]:
    for t in root.find_all(_TEXT_TAGS):
        if isinstance(t, Tag) and t.get_text(strip=True):
            yield t


def _text_excerpt(tag: Tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()[:100]


def _add(findings: list[LintFinding], *, category: str, severity: str,
         message: str, suggested_fix: str, page_index: int,
         tag: Tag | None = None) -> None:
    f = LintFinding(
        category=category, severity=severity, message=message,
        suggested_fix=suggested_fix, page_index=page_index,
    )
    if tag is not None:
        try:
            f.element_ref = ensure_element_identifier(tag)
        except Exception:
            pass
        try:
            f.selector_hint = build_selector_hint(tag, tag.find_parent("body") or tag)
        except Exception:
            pass
        f.excerpt = _text_excerpt(tag)
    findings.append(f)


def _resolve_tag_color(tag: Tag, prop: str, variables: dict[str, str],
                       fallback: str | None) -> tuple[tuple[float, float, float] | None, str | None]:
    """Walk up ancestors until a matching style color is found; resolve
    var(--x) via ``variables``. Returns (rgb01, raw_string)."""
    cur: Tag | None = tag
    while cur is not None and isinstance(cur, Tag):
        style = _parse_style(cur.get("style", "") or "")
        raw = None
        if prop == "color":
            raw = style.get("color")
        else:
            raw = style.get("background-color") or style.get("background")
        if raw:
            rgb = _resolve_color(raw, variables)
            if rgb is not None:
                return rgb, raw
        cur = cur.parent
    if fallback:
        rgb = _resolve_color(fallback, variables)
        return rgb, fallback
    return None, None


def _lint_contrast(root: Tag | BeautifulSoup, variables: dict[str, str],
                   body_color: str, body_bg: str,
                   findings: list[LintFinding], page_index: int) -> None:
    seen = 0
    for tag in _iter_text_tags(root):
        if seen >= 8:  # cap per page
            break
        fg_rgb, _fg_raw = _resolve_tag_color(tag, "color", variables, body_color)
        bg_rgb, _bg_raw = _resolve_tag_color(tag, "bg", variables, body_bg)
        if fg_rgb is None or bg_rgb is None:
            continue
        ratio = _contrast_ratio(fg_rgb, bg_rgb)
        if ratio >= 4.5:
            continue
        severity = "high" if ratio < 3.0 else "medium"
        _add(findings,
             category="contrast", severity=severity,
             message=(f"Text has {ratio:.2f}:1 contrast against its background "
                      f"(WCAG AA requires 4.5:1 for body copy)."),
             suggested_fix=("Darken the text or lighten the background. "
                            "Run designer_apply_repairs with categories=['contrast']."),
             page_index=page_index, tag=tag)
        seen += 1


def _collect_page_hexes(html: str) -> list[str]:
    out: list[str] = []
    for m in _HEX_RE.finditer(html or ""):
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        out.append("#" + h.lower())
    return out


def _lint_off_palette(page_html: str, brand_hexes: set[str],
                      findings: list[LintFinding], page_index: int) -> None:
    if not brand_hexes:
        return
    found = {}
    for h in _collect_page_hexes(page_html):
        if h in _NEUTRAL_HEXES or h in {"#000000", "#ffffff"}:
            continue
        if h in brand_hexes:
            continue
        # Within tolerance of any brand color? Skip.
        if any(_channel_delta(h, b) < 6 for b in brand_hexes):
            continue
        found[h] = found.get(h, 0) + 1
    for h, count in list(found.items())[:5]:
        _add(findings,
             category="off_palette", severity="low",
             message=(f"Color {h} appears {count}x but is not part of the "
                      f"brand palette."),
             suggested_fix=("Replace with primary/secondary/accent/bg/text "
                            "from the brand, or add it to the palette via "
                            "designer_set_brand."),
             page_index=page_index)


_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;}\"]+)", re.IGNORECASE)


def _lint_fonts(page_html: str, brand_fonts: set[str],
                findings: list[LintFinding], page_index: int) -> None:
    allowed = set(brand_fonts) | _SAFE_FONTS
    seen: set[str] = set()
    for m in _FONT_FAMILY_RE.finditer(page_html or ""):
        stack = m.group(1)
        for part in stack.split(","):
            name = part.strip().strip("\"'").lower()
            if not name or name in allowed or name in seen:
                continue
            seen.add(name)
            _add(findings,
                 category="font", severity="low",
                 message=(f"Font family \"{part.strip()}\" is not part of the "
                          f"brand typography."),
                 suggested_fix=("Use heading_font or body_font from the brand, "
                                "or add it via designer_set_brand."),
                 page_index=page_index)
            if len(seen) >= 5:
                return


def _lint_missing_alt(root: Tag | BeautifulSoup,
                      findings: list[LintFinding], page_index: int) -> None:
    count = 0
    for img in root.find_all("img"):
        if not isinstance(img, Tag):
            continue
        # An explicitly-present ``alt`` attribute (even empty) is the
        # correct WCAG marker for a decorative image; only flag when
        # the attribute is entirely absent.
        if img.has_attr("alt"):
            continue
        count += 1
        if count > 4:
            break
        _add(findings,
             category="missing_alt", severity="medium",
             message="Image is missing an alt description.",
             suggested_fix=("Add a concise alt attribute describing the image. "
                            "Screen readers and brand-safe exports require it."),
             page_index=page_index, tag=img)


_LOGO_CORNERS = {
    "top_left":    ("top", "left"),
    "top_right":   ("top", "right"),
    "bottom_left": ("bottom", "left"),
    "bottom_right":("bottom", "right"),
}


def _lint_logo_safe_zone(root: Tag | BeautifulSoup, brand,
                         findings: list[LintFinding], page_index: int) -> None:
    if brand is None:
        return
    mode = (getattr(brand, "logo_mode", "") or "").lower()
    if mode not in {"auto", "manual"}:
        return
    has_logo = bool(getattr(brand, "logo_b64", None)) or bool(getattr(brand, "logo_asset_id", ""))
    if not has_logo:
        return
    position = (getattr(brand, "logo_position", "") or "top_right").lower()
    corner = _LOGO_CORNERS.get(position)
    if corner is None:
        return
    v_edge, h_edge = corner
    max_h = int(getattr(brand, "logo_max_height", 72) or 72)
    pad = int(getattr(brand, "logo_padding", 24) or 24)
    zone_px = max_h + pad

    flagged = 0
    for el in root.find_all(["div", "section", "img", "video", "h1", "h2", "h3"]):
        if flagged >= 2:
            break
        if not isinstance(el, Tag):
            continue
        style = _parse_style(el.get("style", "") or "")
        pos = (style.get("position", "") or "").strip().lower()
        if pos not in {"absolute", "fixed"}:
            continue
        v_val = style.get(v_edge)
        h_val = style.get(h_edge)
        if v_val is None or h_val is None:
            continue
        try:
            v_px = float(re.sub(r"[^\d.\-]", "", v_val) or "9999")
            h_px = float(re.sub(r"[^\d.\-]", "", h_val) or "9999")
        except ValueError:
            continue
        if v_px <= zone_px and h_px <= zone_px:
            flagged += 1
            _add(findings,
                 category="logo_safe_zone", severity="low",
                 message=(f"An element overlaps the {position} logo safe zone "
                          f"(~{zone_px}px)."),
                 suggested_fix=(f"Move the element away from the {position} "
                                f"corner or shrink logo_max_height/logo_padding."),
                 page_index=page_index, tag=el)


# ── Public API ───────────────────────────────────────────────────────────

def lint_page(page_html: str, *, brand, page_index: int = 0) -> list[LintFinding]:
    """Scan one page's HTML. ``brand`` may be ``None``."""
    soup = BeautifulSoup(page_html or "", "html.parser")
    root = soup.body or soup
    variables = _extract_css_variables(page_html)
    body = soup.body if isinstance(soup.body, Tag) else root
    body_style = _parse_style(body.get("style", "") if isinstance(body, Tag) else "")
    body_color = body_style.get("color") or "var(--text)"
    body_bg = body_style.get("background-color") or body_style.get("background") or "var(--bg)"

    findings: list[LintFinding] = []
    _lint_contrast(root, variables, body_color, body_bg, findings, page_index)
    _lint_off_palette(page_html, _brand_hexes(brand), findings, page_index)
    _lint_fonts(page_html, _brand_fonts(brand), findings, page_index)
    _lint_missing_alt(root, findings, page_index)
    _lint_logo_safe_zone(root, brand, findings, page_index)
    return findings


def lint_project(project) -> dict[str, Any]:
    """Scan every page in a project. Returns a dict report."""
    pages = getattr(project, "pages", None) or []
    brand = getattr(project, "brand", None)
    all_findings: list[LintFinding] = []
    for idx, page in enumerate(pages):
        try:
            html = getattr(page, "html", "") or ""
            all_findings.extend(lint_page(html, brand=brand, page_index=idx))
        except Exception:
            continue

    category_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    for f in all_findings:
        category_counts[f.category] = category_counts.get(f.category, 0) + 1
        if f.severity in severity_counts:
            severity_counts[f.severity] += 1

    summary = (
        "No brand issues detected." if not all_findings
        else f"{len(all_findings)} brand issue(s): "
             + ", ".join(f"{category_counts[k]} {k}" for k in sorted(category_counts))
    )
    return {
        "findings": [f.to_dict() for f in all_findings],
        "summary": summary,
        "category_counts": category_counts,
        "severity_counts": severity_counts,
    }


# ── Deterministic repairers (Phase 2.1.I) ────────────────────────────────

_BRAND_AUTO_CATEGORIES = {"contrast", "off_palette", "font", "missing_alt"}
_BRAND_ALL_CATEGORIES = set(_LINT_CATEGORIES)


def _nearest_brand_hex(hex_color: str, brand_hexes: set[str]) -> str | None:
    """Return the brand hex with the smallest per-channel distance."""
    if not brand_hexes:
        return None
    best: tuple[int, str] | None = None
    for b in brand_hexes:
        d = _channel_delta(hex_color, b)
        if best is None or d < best[0]:
            best = (d, b)
    return best[1] if best else None


_STYLE_HEX_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")


def _rewrite_hex_in_style(style_attr: str, brand_hexes: set[str]) -> tuple[str, int]:
    """Replace off-palette hex values in a style string with the nearest
    brand color. Returns (new_style, replacements_count)."""
    if not style_attr or not brand_hexes:
        return style_attr, 0
    replaced = 0

    def _sub(m: re.Match) -> str:
        nonlocal replaced
        raw = m.group(1)
        full = raw if len(raw) == 6 else "".join(c * 2 for c in raw)
        h = "#" + full.lower()
        if h in brand_hexes or h in _NEUTRAL_HEXES or h in {"#000000", "#ffffff"}:
            return m.group(0)
        if any(_channel_delta(h, b) < 6 for b in brand_hexes):
            return m.group(0)
        nearest = _nearest_brand_hex(h, brand_hexes)
        if not nearest:
            return m.group(0)
        replaced += 1
        return nearest

    new = _STYLE_HEX_RE.sub(_sub, style_attr)
    return new, replaced


def _repair_off_palette(root: Tag | BeautifulSoup, brand,
                        changes: list[dict]) -> None:
    brand_hexes = _brand_hexes(brand)
    if not brand_hexes:
        return
    for el in root.find_all(True):
        if not isinstance(el, Tag):
            continue
        style = el.get("style", "") or ""
        if not style or "#" not in style:
            continue
        new_style, n = _rewrite_hex_in_style(style, brand_hexes)
        if n and new_style != style:
            el["style"] = new_style
            _record_change(changes, "off_palette", el,
                           f"Replaced {n} off-palette color(s) with nearest brand color.")

    # Also rewrite hex colors inside <style> blocks (CSS rules and
    # ``--var`` declarations). Without this the lint scanner — which
    # reads the whole page HTML — keeps re-flagging the same values.
    # We walk the document root (not just body) so <style> in <head> is
    # included.
    document = root
    try:
        parent = getattr(root, "parent", None)
        while parent is not None:
            document = parent
            parent = getattr(parent, "parent", None)
    except Exception:
        pass
    for style_tag in document.find_all("style"):
        if not isinstance(style_tag, Tag):
            continue
        original = style_tag.string or style_tag.get_text() or ""
        if "#" not in original:
            continue
        new_css, n = _rewrite_hex_in_style(original, brand_hexes)
        if n and new_css != original:
            style_tag.string = new_css
            _record_change(changes, "off_palette", style_tag,
                           f"Replaced {n} off-palette color(s) in stylesheet.")


_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _rewrite_font_family_in_css(
    css: str, body_font: str, heading_font: str, allowed: set[str]
) -> tuple[str, int]:
    """Rewrite off-brand font-family declarations inside a CSS string.

    Used for ``<style>`` tag content. Returns (new_css, replacements)."""
    if not css or "font-family" not in css.lower():
        return css, 0
    target = body_font or heading_font
    if not target:
        return css, 0
    replaced = 0

    def _sub(m: "re.Match") -> str:
        nonlocal replaced
        stack = m.group(1)
        parts = [p.strip().strip("\"'") for p in stack.split(",")]
        primary = parts[0].lower() if parts else ""
        if not primary or primary in allowed:
            return m.group(0)
        fallback = "sans-serif"
        for p in reversed(parts):
            pl = p.lower()
            if pl in {"serif", "sans-serif", "monospace", "cursive", "fantasy"}:
                fallback = pl
                break
        new_family = f"{target}, {fallback}" if "," not in target else target
        replaced += 1
        return f"font-family: {new_family}"

    new_css = _FONT_FAMILY_RE.sub(_sub, css)
    return new_css, replaced


def _repair_fonts(root: Tag | BeautifulSoup, brand,
                  changes: list[dict]) -> None:
    if brand is None:
        return
    body_font = (getattr(brand, "body_font", "") or "").strip()
    heading_font = (getattr(brand, "heading_font", "") or body_font).strip()
    if not body_font and not heading_font:
        return
    brand_fonts = _brand_fonts(brand)
    allowed = brand_fonts | _SAFE_FONTS

    for el in root.find_all(True):
        if not isinstance(el, Tag):
            continue
        style = el.get("style", "") or ""
        if "font-family" not in style.lower():
            continue
        parsed = _parse_style(style)
        current = (parsed.get("font-family") or "").strip()
        if not current:
            continue
        # Flag on the primary family only; fallbacks are allowed to stay.
        parts = [p.strip().strip("\"'") for p in current.split(",")]
        primary = parts[0].lower() if parts else ""
        if not primary or primary in allowed:
            continue
        target = heading_font if el.name in _HEADING_TAGS else body_font
        if not target:
            continue
        # Preserve generic fallback if present at the end.
        fallback = "sans-serif"
        for p in reversed(parts):
            pl = p.lower()
            if pl in {"serif", "sans-serif", "monospace", "cursive", "fantasy"}:
                fallback = pl
                break
        new_family = f"{target}, {fallback}" if "," not in target else target
        if _update_style(el, **{"font-family": new_family}):
            _record_change(changes, "font", el,
                           f"Swapped off-brand font-family to \"{target}\".")

    # Also rewrite font-family declarations inside <style> tags so the
    # lint scanner (which regex-scans the full HTML) stops re-flagging
    # them.
    document = root
    try:
        parent = getattr(root, "parent", None)
        while parent is not None:
            document = parent
            parent = getattr(parent, "parent", None)
    except Exception:
        pass
    for style_tag in document.find_all("style"):
        if not isinstance(style_tag, Tag):
            continue
        original = style_tag.string or style_tag.get_text() or ""
        new_css, n = _rewrite_font_family_in_css(
            original, body_font, heading_font, allowed,
        )
        if n and new_css != original:
            style_tag.string = new_css
            _record_change(changes, "font", style_tag,
                           f"Swapped {n} off-brand font-family declaration(s) "
                           f"in stylesheet.")


def _infer_alt(img: Tag, page_title: str = "") -> str:
    """Pick a conservative alt text. Empty string = decorative."""
    # Prefer title attr
    title = (img.get("title") or "").strip()
    if title:
        return title[:120]
    # Parent anchor/button text
    for parent in img.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in {"a", "button"}:
            txt = parent.get_text(" ", strip=True)
            if txt:
                return txt[:120]
            break
    # Sibling <figcaption> inside a <figure>
    fig = img.find_parent("figure")
    if isinstance(fig, Tag):
        cap = fig.find("figcaption")
        if isinstance(cap, Tag):
            txt = cap.get_text(" ", strip=True)
            if txt:
                return txt[:120]
    # aria-label on image itself
    aria = (img.get("aria-label") or "").strip()
    if aria:
        return aria[:120]
    # Decorative default
    return ""


def _repair_missing_alt(root: Tag | BeautifulSoup, page_title: str,
                        changes: list[dict]) -> None:
    for img in root.find_all("img"):
        if not isinstance(img, Tag):
            continue
        # An explicit ``alt`` attribute — even empty — is the intended
        # marker for a decorative image. Skip so we don't record no-op
        # changes every time the repair runs.
        if img.has_attr("alt"):
            continue
        alt = _infer_alt(img, page_title)
        img["alt"] = alt
        desc = (f"Set alt=\"{alt[:40]}…\"" if len(alt) > 40
                else (f"Set alt=\"{alt}\"" if alt
                      else "Marked decorative image (alt=\"\")."))
        _record_change(changes, "missing_alt", img, desc)


def _normalize_brand_categories(categories: list[str] | str | None) -> set[str]:
    if categories is None:
        return set(_BRAND_AUTO_CATEGORIES)
    if isinstance(categories, str):
        raw = [t.strip().lower() for t in categories.split(",") if t.strip()]
    else:
        raw = [str(t).strip().lower() for t in categories if str(t).strip()]
    selected = set(raw) if raw else set(_BRAND_AUTO_CATEGORIES)
    invalid = selected - _BRAND_ALL_CATEGORIES
    if invalid:
        valid = ", ".join(sorted(_BRAND_ALL_CATEGORIES))
        raise ValueError(
            f"Unknown brand-lint categories: {', '.join(sorted(invalid))}. Valid: {valid}."
        )
    return selected & _BRAND_AUTO_CATEGORIES  # logo_safe_zone silently dropped


def apply_brand_repairs_to_html(
    page_html: str,
    brand,
    categories: list[str] | str | None = None,
    *,
    page_title: str = "",
) -> tuple[str, list[dict]]:
    """Apply safe deterministic brand-lint repairs.

    Supports: off_palette, font, missing_alt, contrast.
    ``logo_safe_zone`` is never auto-fixed and is silently skipped.
    """
    selected = _normalize_brand_categories(categories)
    if not selected:
        return page_html, []
    soup = BeautifulSoup(page_html or "", "html.parser")
    root = soup.body or soup
    variables = _extract_css_variables(page_html)
    body = soup.body if isinstance(soup.body, Tag) else root
    body_style = _parse_style(body.get("style", "") if isinstance(body, Tag) else "")
    body_bg_raw = body_style.get("background-color") or body_style.get("background") or "var(--bg)"

    changes: list[dict] = []
    if "off_palette" in selected:
        _repair_off_palette(root, brand, changes)
    if "font" in selected:
        _repair_fonts(root, brand, changes)
    if "missing_alt" in selected:
        _repair_missing_alt(root, page_title, changes)
    if "contrast" in selected:
        _repair_contrast(root, variables, body_bg_raw, changes)

    if not changes:
        return page_html, []
    return str(soup), changes


# ── NiceGUI dialog ───────────────────────────────────────────────────────

_SEVERITY_COLORS = {"high": "red-6", "medium": "orange-7", "low": "grey-6"}


def open_brand_lint_dialog(project) -> None:
    """Open a read-only dialog listing brand-lint findings."""
    from nicegui import ui

    with ui.dialog() as dlg, ui.card().style(
        "min-width: 560px; max-width: 720px; padding: 12px 14px;"
    ):
        header_row = ui.row().classes("w-full items-center justify-between no-wrap")
        body_col = ui.column().classes("w-full gap-0").style(
            "max-height: 420px; overflow-y: auto; margin-top: 8px;"
        )
        footer = ui.row().classes("w-full items-center justify-end q-mt-sm")

        def _render() -> None:
            header_row.clear()
            body_col.clear()
            report = lint_project(project)
            findings = report["findings"]
            with header_row:
                with ui.column().classes("gap-0"):
                    ui.label("Brand lint").classes("text-subtitle1 text-weight-medium")
                    ui.label(report["summary"]).classes("text-xs text-grey-6")
                if findings:
                    sc = report["severity_counts"]
                    with ui.row().classes("gap-1"):
                        if sc.get("high"):
                            ui.badge(f"{sc['high']} high").props("color=red-6")
                        if sc.get("medium"):
                            ui.badge(f"{sc['medium']} medium").props("color=orange-7")
                        if sc.get("low"):
                            ui.badge(f"{sc['low']} low").props("color=grey-6")
            with body_col:
                if not findings:
                    ui.label("Everything looks on-brand.").classes(
                        "text-grey-5 text-sm q-pa-md"
                    )
                    return
                for f in findings:
                    card = ui.row().classes(
                        "w-full items-start no-wrap q-pa-sm"
                    ).style("border-bottom: 1px solid #eee; gap: 10px;")
                    with card:
                        ui.badge(f["severity"]).props(
                            f"color={_SEVERITY_COLORS.get(f['severity'], 'grey-6')}"
                        ).classes("q-mt-xs")
                        with ui.column().classes("gap-0"):
                            ui.label(
                                f"Page {f['page_index'] + 1} · {f['category']}"
                            ).classes("text-xs text-grey-6")
                            ui.label(f["message"]).classes("text-sm")
                            if f.get("suggested_fix"):
                                ui.label(f["suggested_fix"]).classes(
                                    "text-xs text-grey-7"
                                )
                            if f.get("excerpt"):
                                ui.label(f"\u201c{f['excerpt']}\u201d").classes(
                                    "text-xs text-grey-5"
                                ).style("font-style: italic;")

        with footer:
            ui.button("Re-scan", icon="refresh", on_click=_render).props("flat")
            ui.button("Close", on_click=dlg.close).props("flat")

        _render()
        dlg.open()
