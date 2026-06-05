"""Designer — brand extraction, presets, and brand management utilities."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.designer.state import BrandConfig

logger = logging.getLogger(__name__)

_BRAND_DIR = get_row_bot_data_dir() / "designer" / "brands"


# ═══════════════════════════════════════════════════════════════════════
# BUILT-IN PRESETS
# ═══════════════════════════════════════════════════════════════════════

BRAND_PRESETS: dict[str, BrandConfig] = {
    "Default Dark": BrandConfig(),
    "Ocean Blue": BrandConfig(
        primary_color="#0EA5E9", secondary_color="#0284C7",
        accent_color="#38BDF8", bg_color="#0C1929", text_color="#E0F2FE",
    ),
    "Forest Green": BrandConfig(
        primary_color="#22C55E", secondary_color="#15803D",
        accent_color="#86EFAC", bg_color="#0A1F0E", text_color="#F0FDF4",
    ),
    "Sunset Orange": BrandConfig(
        primary_color="#F97316", secondary_color="#EA580C",
        accent_color="#FDBA74", bg_color="#1C0F05", text_color="#FFF7ED",
    ),
    "Purple Haze": BrandConfig(
        primary_color="#A855F7", secondary_color="#7E22CE",
        accent_color="#C084FC", bg_color="#140A24", text_color="#FAF5FF",
    ),
    "Minimalist Light": BrandConfig(
        primary_color="#1F2937", secondary_color="#374151",
        accent_color="#4F78A4", bg_color="#FFFFFF", text_color="#111827",
        heading_font="Georgia", body_font="Georgia",
    ),
    "Corporate": BrandConfig(
        primary_color="#1E3A5F", secondary_color="#0F2440",
        accent_color="#D4AF37", bg_color="#0D1B2A", text_color="#E0E7EF",
        heading_font="Merriweather", body_font="Inter",
    ),
    "Neon": BrandConfig(
        primary_color="#00FF88", secondary_color="#00CC6A",
        accent_color="#FF00FF", bg_color="#0A0A0A", text_color="#FFFFFF",
        heading_font="Orbitron", body_font="Inter",
    ),
    "Aurora UI": BrandConfig(
        primary_color="#14B8A6", secondary_color="#0F766E",
        accent_color="#8B5CF6", bg_color="#07131E", text_color="#ECFEFF",
        heading_font="Space Grotesk", body_font="Inter",
    ),
    "Rose Studio": BrandConfig(
        primary_color="#F43F5E", secondary_color="#BE123C",
        accent_color="#FDBA74", bg_color="#19060E", text_color="#FFF1F2",
        heading_font="Poppins", body_font="DM Sans",
    ),
    "Cobalt Paper": BrandConfig(
        primary_color="#2563EB", secondary_color="#1D4ED8",
        accent_color="#F97316", bg_color="#F8FAFC", text_color="#0F172A",
        heading_font="Plus Jakarta Sans", body_font="Inter",
    ),
    "Graphite Mint": BrandConfig(
        primary_color="#10B981", secondary_color="#047857",
        accent_color="#A7F3D0", bg_color="#0B0F12", text_color="#ECFDF5",
        heading_font="DM Sans", body_font="Inter",
    ),
    "Lime Grid": BrandConfig(
        primary_color="#84CC16", secondary_color="#4D7C0F",
        accent_color="#FACC15", bg_color="#10150A", text_color="#F7FEE7",
        heading_font="Space Grotesk", body_font="Inter",
    ),
    "Editorial Slate": BrandConfig(
        primary_color="#111827", secondary_color="#374151",
        accent_color="#9CA3AF", bg_color="#FAFAF9", text_color="#111827",
        heading_font="Playfair Display", body_font="Inter",
    ),
    "Solar Flare": BrandConfig(
        primary_color="#F59E0B", secondary_color="#EA580C",
        accent_color="#FB7185", bg_color="#1A0F07", text_color="#FFF7ED",
        heading_font="Bebas Neue", body_font="Inter",
    ),
    "Midnight Signal": BrandConfig(
        primary_color="#38BDF8", secondary_color="#0EA5E9",
        accent_color="#F43F5E", bg_color="#020617", text_color="#E2E8F0",
        heading_font="Space Grotesk", body_font="IBM Plex Mono",
    ),
}


# ═══════════════════════════════════════════════════════════════════════
# SAVE / LOAD CUSTOM PRESETS
# ═══════════════════════════════════════════════════════════════════════

def save_brand_preset(name: str, brand: BrandConfig) -> Path:
    """Save a brand config as a named preset."""
    _BRAND_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)[:50]
    path = _BRAND_DIR / f"{safe}.json"
    path.write_text(json.dumps({"name": name, **brand.to_dict()}, indent=2))
    logger.info("Saved brand preset '%s' to %s", name, path)
    return path


def load_brand_presets() -> dict[str, BrandConfig]:
    """Return all custom brand presets from disk."""
    result: dict[str, BrandConfig] = {}
    if not _BRAND_DIR.exists():
        return result
    for p in sorted(_BRAND_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            name = data.pop("name", p.stem)
            result[name] = BrandConfig.from_dict(data)
        except Exception:
            logger.warning("Skipping invalid brand preset: %s", p)
    return result


def delete_brand_preset(name: str) -> bool:
    """Delete a custom preset by name."""
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)[:50]
    path = _BRAND_DIR / f"{safe}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def get_all_presets() -> dict[str, BrandConfig]:
    """Built-in + custom presets merged (custom can override built-in names)."""
    merged = dict(BRAND_PRESETS)
    merged.update(load_brand_presets())
    return merged


# ═══════════════════════════════════════════════════════════════════════
# BRAND EXTRACTION FROM URL
# ═══════════════════════════════════════════════════════════════════════

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_FONT_FAMILY_RE = re.compile(
    r"font-family\s*:\s*['\"]?([A-Za-z][A-Za-z0-9 _-]+)", re.IGNORECASE,
)


def extract_brand_from_url(url: str) -> Optional[BrandConfig]:
    """Fetch a URL and attempt to extract brand colors/fonts from inline CSS.

    This is a best-effort heuristic — not guaranteed to be perfect.
    Returns None on failure.
    """
    try:
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Row-Bot-Designer/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read(512_000).decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Brand extraction failed to fetch %s: %s", url, exc)
        return None

    # Collect colors (from CSS custom properties, inline styles, etc.)
    colors = _HEX_RE.findall(html)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_colors: list[str] = []
    for c in colors:
        norm = c.upper()
        if norm not in seen and norm not in ("#FFFFFF", "#000000", "#FFF", "#000"):
            seen.add(norm)
            unique_colors.append(c)

    # Collect fonts
    fonts = _FONT_FAMILY_RE.findall(html)
    unique_fonts: list[str] = []
    seen_fonts: set[str] = set()
    ignore_fonts = {"inherit", "initial", "sans-serif", "serif", "monospace", "system-ui"}
    for f in fonts:
        name = f.strip().strip("'\"")
        if name.lower() not in seen_fonts and name.lower() not in ignore_fonts:
            seen_fonts.add(name.lower())
            unique_fonts.append(name)

    if not unique_colors and not unique_fonts:
        return None

    brand = BrandConfig()
    if len(unique_colors) >= 1:
        brand.primary_color = unique_colors[0]
    if len(unique_colors) >= 2:
        brand.secondary_color = unique_colors[1]
    if len(unique_colors) >= 3:
        brand.accent_color = unique_colors[2]
    if len(unique_fonts) >= 1:
        brand.heading_font = unique_fonts[0]
    if len(unique_fonts) >= 2:
        brand.body_font = unique_fonts[1]
    elif len(unique_fonts) == 1:
        brand.body_font = unique_fonts[0]

    logger.info(
        "Extracted brand from %s: %d colors, %d fonts",
        url, len(unique_colors), len(unique_fonts),
    )
    return brand
