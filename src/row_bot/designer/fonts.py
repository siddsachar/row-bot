"""Designer — hybrid font resolver (offline-first, online-enhanced).

Three-tier resolution:
  1. static/fonts/   — 25 bundled fonts (always available)
  2. ~/.row-bot/font_cache/  — previously downloaded fonts
  3. Google Fonts CDN — fallback when online
  4. Closest bundled fallback font

Public API:
  get_font_css(family, base_url)  → @font-face CSS for best available source
  get_all_fonts_css(families, base_url) → combined CSS for multiple families
  ensure_font(family)             → download + cache if not bundled
  list_available_fonts()          → [{name, source, weights, category}]
  is_font_available_offline(name) → bool
  BUNDLED_FONTS                   → dict of bundled font metadata
"""

from __future__ import annotations

import json
import logging
import re
import os
import stat
from itertools import islice
from contextlib import contextmanager
from contextvars import ContextVar
import urllib.request
from pathlib import Path
from typing import Optional

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.runtime_paths import static_dir

logger = logging.getLogger(__name__)
_STRICT_OFFLINE_FONTS: ContextVar[bool] = ContextVar('designer_strict_offline_fonts', default=False)


@contextmanager
def strict_offline_fonts():
    """Suppress intermediate URL CSS; the final isolated owner embeds fonts."""
    token = _STRICT_OFFLINE_FONTS.set(True)
    try:
        yield
    finally:
        _STRICT_OFFLINE_FONTS.reset(token)

# ── Paths ─────────────────────────────────────────────────────────────────
_STATIC_DIR = static_dir()
_FONTS_DIR = _STATIC_DIR / "fonts"
_MANIFEST_PATH = _FONTS_DIR / "manifest.json"
_CACHE_DIR = get_row_bot_data_dir() / "font_cache"

# ── Load manifest ─────────────────────────────────────────────────────────
_manifest: dict[str, dict[str, str]] = {}
if _MANIFEST_PATH.exists():
    try:
        _manifest = json.loads(_MANIFEST_PATH.read_text())
    except Exception:
        logger.warning("Failed to load font manifest")


def _safe_dirname(family: str) -> str:
    return family.lower().replace(" ", "-")


# ── Bundled font registry ─────────────────────────────────────────────────

# Category hints for UI grouping
_CATEGORIES: dict[str, str] = {
    "Inter": "Sans", "Roboto": "Sans", "Open Sans": "Sans", "Lato": "Sans",
    "Montserrat": "Sans", "Poppins": "Sans", "Raleway": "Sans", "Nunito": "Sans",
    "Source Sans 3": "Sans", "Work Sans": "Sans", "DM Sans": "Sans",
    "Plus Jakarta Sans": "Sans",
    "Merriweather": "Serif", "Playfair Display": "Serif", "Lora": "Serif",
    "PT Serif": "Serif", "Libre Baskerville": "Serif",
    "Orbitron": "Display", "Bebas Neue": "Display", "Oswald": "Display",
    "Anton": "Display", "Space Grotesk": "Display",
    "Fira Code": "Mono", "JetBrains Mono": "Mono", "IBM Plex Mono": "Mono",
}

# System fallback chains by category
_FALLBACKS: dict[str, str] = {
    "Sans": "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
    "Serif": "Georgia, 'Times New Roman', Times, serif",
    "Display": "system-ui, -apple-system, 'Segoe UI', sans-serif",
    "Mono": "'Cascadia Code', 'Fira Code', Consolas, 'Courier New', monospace",
}


def get_fallback_stack(family: str) -> str:
    """Return the CSS fallback font stack for a family."""
    cat = _CATEGORIES.get(family, "Sans")
    return _FALLBACKS.get(cat, _FALLBACKS["Sans"])


# ═══════════════════════════════════════════════════════════════════════════
# FONT CSS GENERATION
# ═══════════════════════════════════════════════════════════════════════════

def get_font_css(family: str, base_url: str = "/static") -> str:
    """Generate @font-face CSS for a font family.

    Resolution order: bundled → cached → CDN fallback.

    Parameters
    ----------
    family : str
        Font family name (e.g. "Inter", "Roboto").
    base_url : str
        Base URL prefix for static files (default "/static").
        For HTML export, pass "." and embed separately.

    Returns
    -------
    str
        CSS @font-face declarations.
    """
    if _STRICT_OFFLINE_FONTS.get():
        return ''  # Final strict embedding validates and resolves exactly once.
    # Tier 1: Bundled
    if family in _manifest:
        return _bundled_font_css(family, base_url)

    # Tier 2: Cached
    cache_dir = _CACHE_DIR / _safe_dirname(family)
    if cache_dir.exists():
        cached_files = sorted(cache_dir.glob("*.woff2"))
        if cached_files:
            return _cached_font_css(family, cached_files)

    # Tier 3: Google Fonts CDN (online fallback)
    return _cdn_font_css(family)


def get_all_fonts_css(families: list[str], base_url: str = "/static") -> str:
    """Generate combined @font-face CSS for multiple font families."""
    seen = set()
    blocks = []
    for fam in families:
        if fam and fam not in seen:
            seen.add(fam)
            blocks.append(get_font_css(fam, base_url))
    return "\n".join(blocks)


def _bundled_font_css(family: str, base_url: str) -> str:
    """Build @font-face for a bundled font."""
    dirname = _safe_dirname(family)
    weights = _manifest[family]
    lines = []
    for weight_str, filename in sorted(weights.items()):
        url = f"{base_url}/fonts/{dirname}/{filename}"
        lines.append(
            f"@font-face {{\n"
            f"  font-family: '{family}';\n"
            f"  font-style: normal;\n"
            f"  font-weight: {weight_str};\n"
            f"  font-display: swap;\n"
            f"  src: url('{url}') format('woff2');\n"
            f"}}"
        )
    return "\n".join(lines)


def _cached_font_css(family: str, files: list[Path]) -> str:
    """Build @font-face for a cached font."""
    dirname = _safe_dirname(family)
    lines = []
    weight_re = re.compile(r"-(\d+)\.woff2$")
    for f in files:
        m = weight_re.search(f.name)
        weight = m.group(1) if m else "400"
        url = f"/_fonts/cache/{dirname}/{f.name}"
        lines.append(
            f"@font-face {{\n"
            f"  font-family: '{family}';\n"
            f"  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            f"  font-display: swap;\n"
            f"  src: url('{url}') format('woff2');\n"
            f"}}"
        )
    return "\n".join(lines)


def _cdn_font_css(family: str) -> str:
    """Return a Google Fonts @import as online-only fallback."""
    encoded = family.replace(" ", "+")
    return (
        f"/* Online fallback for '{family}' */\n"
        f"@import url('https://fonts.googleapis.com/css2?"
        f"family={encoded}:wght@300;400;600;700&display=swap');"
    )


# ═══════════════════════════════════════════════════════════════════════════
# FONT CSS FOR EXPORT (base64 embedded)
# ═══════════════════════════════════════════════════════════════════════════

def get_font_css_embedded(family: str, *, strict: bool = False) -> str:
    """Generate @font-face CSS with base64-embedded woff2 data.

    For self-contained HTML export. Falls back to CDN if not bundled/cached.
    """
    import base64

    if strict:
        return _strict_font_css(family)

    # Try bundled first
    if family in _manifest:
        dirname = _safe_dirname(family)
        weights = _manifest[family]
        lines = []
        for weight_str, filename in sorted(weights.items()):
            fpath = _FONTS_DIR / dirname / filename
            if fpath.exists():
                b64 = base64.b64encode(fpath.read_bytes()).decode()
                lines.append(
                    f"@font-face {{\n"
                    f"  font-family: '{family}';\n"
                    f"  font-style: normal;\n"
                    f"  font-weight: {weight_str};\n"
                    f"  font-display: swap;\n"
                    f"  src: url('data:font/woff2;base64,{b64}') format('woff2');\n"
                    f"}}"
                )
        if lines:
            return "\n".join(lines)

    # Try cached
    cache_dir = _CACHE_DIR / _safe_dirname(family)
    if cache_dir.exists():
        files = sorted(cache_dir.glob("*.woff2"))
        if files:
            lines = []
            weight_re = re.compile(r"-(\d+)\.woff2$")
            for f in files:
                m = weight_re.search(f.name)
                weight = m.group(1) if m else "400"
                b64 = base64.b64encode(f.read_bytes()).decode()
                lines.append(
                    f"@font-face {{\n"
                    f"  font-family: '{family}';\n"
                    f"  font-style: normal;\n"
                    f"  font-weight: {weight};\n"
                    f"  font-display: swap;\n"
                    f"  src: url('data:font/woff2;base64,{b64}') format('woff2');\n"
                    f"}}"
                )
            return "\n".join(lines)

    # Fallback to CDN import
    return _cdn_font_css(family)


class FontReadError(ValueError):
    """An offline font could not be safely and completely resolved."""


_FONT_FILE_BYTES = 4 * 1024 * 1024
_FONT_FAMILY_BYTES = 8 * 1024 * 1024
_FONT_MAX_FILES = 64
_SYSTEM_FONTS = {'Arial', 'Georgia', 'Times New Roman', 'system-ui', 'serif', 'sans-serif', 'monospace'}


def _font_files(family: str):
    if not isinstance(family, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9 -]{0,127}', family):
        raise FontReadError('font_unavailable')
    if family in _SYSTEM_FONTS:
        return []
    bundled = family in _manifest
    root = (_FONTS_DIR if bundled else _CACHE_DIR) / _safe_dirname(family)
    if any(path.is_symlink() or path.is_junction() for path in (root, *root.parents)):
        raise FontReadError('font_unsafe_path')
    if not root.is_dir():
        raise FontReadError('font_unavailable')
    parent = root.stat()
    if bundled:
        weights = _manifest[family]
        if not isinstance(weights, dict) or not 0 < len(weights) <= _FONT_MAX_FILES:
            raise FontReadError('font_catalog_invalid')
        entries = list(weights.items())
    else:
        paths = list(islice(root.iterdir(), _FONT_MAX_FILES + 1))
        if len(paths) > _FONT_MAX_FILES:
            raise FontReadError('font_budget_exceeded')
        entries = []
        for path in paths:
            if path.suffix == '.woff2':
                match = re.search(r'-(\d+)\.woff2$', path.name)
                entries.append((match[1] if match else '400', path.name))
    if not entries:
        raise FontReadError('font_unavailable')
    result, total = [], 0
    for weight, filename in sorted(entries):
        if (not isinstance(weight, str) or not re.fullmatch(r'[1-9][0-9]{0,3}', weight) or int(weight) > 1000
                or not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}\.woff2', filename)):
            raise FontReadError('font_catalog_invalid')
        path = root / filename
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or path.is_junction()
                or not 48 <= before.st_size <= _FONT_FILE_BYTES):
            raise FontReadError('font_file_invalid')
        total += before.st_size
        if total > _FONT_FAMILY_BYTES:
            raise FontReadError('font_budget_exceeded')
        result.append((weight, path, before, parent))
    if not os.path.samestat(parent, root.stat()):
        raise FontReadError('font_revision_conflict')
    return result


def offline_font_fingerprint(families: list[str]) -> tuple:
    """Bounded saved file identities for client preview invalidation, no cache."""
    if len(families) > 8:
        raise FontReadError('font_budget_exceeded')
    try:
        return tuple((family, tuple((weight, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                     info.st_ctime_ns, parent.st_dev, parent.st_ino)
                    for weight, _path, info, parent in _font_files(family)))
                     for family in dict.fromkeys(families))
    except OSError:
        raise FontReadError('font_unavailable') from None


def _strict_font_css(family: str) -> str:
    import base64
    from row_bot.developer.client_workspace import _empty_parent_guard
    try:
        files = _font_files(family)
        lines = []
        for weight, path, before, parent in files:
            expected = f'{parent.st_dev}:{parent.st_ino}:0'
            # Guard all ancestors on Windows; descriptor-relative leaf open on
            # POSIX ensures a renamed parent cannot redirect a font read.
            with _empty_parent_guard(path.parent, expected) as directory:
                fd = os.open(path.name if directory is not None else path,
                             os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory)
                with os.fdopen(fd, 'rb') as handle:
                    opened = os.fstat(handle.fileno())
                    if not os.path.samestat(before, opened) or opened.st_nlink != 1:
                        raise FontReadError('font_revision_conflict')
                    data = handle.read(_FONT_FILE_BYTES + 1)
                    finished = os.fstat(handle.fileno())
                named = os.stat(path.name, dir_fd=directory, follow_symlinks=False) if directory is not None else path.lstat()
                if (not os.path.samestat(opened, named) or not os.path.samestat(parent, path.parent.lstat())
                        or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (finished.st_size, finished.st_mtime_ns, finished.st_ctime_ns)
                        # Windows pathname stat exposes birth time as ctime in
                        # this supported Python, while fstat reports change time.
                        # Compare change time on the same open handle above.
                        or (named.st_size, named.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)
                        or len(data) != before.st_size or named.st_nlink != 1):
                    raise FontReadError('font_revision_conflict')
            # Validate the bounded WOFF2 container, without invoking a native
            # font decoder. Browser font decoding remains inside its sandbox.
            if (len(data) < 48 or data[:4] != b'wOF2' or int.from_bytes(data[8:12], 'big') != len(data)
                    or not 1 <= int.from_bytes(data[12:14], 'big') <= 4096 or data[14:16] != b'\0\0'
                    or not 1 <= int.from_bytes(data[16:20], 'big') <= 64 * 1024 * 1024
                    or int.from_bytes(data[20:24], 'big') > len(data)):
                raise FontReadError('font_file_invalid')
            encoded = base64.b64encode(data).decode('ascii')
            lines.append(f"@font-face {{ font-family: '{family}'; font-style: normal; font-weight: {weight}; "
                         f"font-display: swap; src: url('data:font/woff2;base64,{encoded}') format('woff2'); }}")
        return '\n'.join(lines)
    except FontReadError:
        raise
    except (OSError, ValueError):
        raise FontReadError('font_unavailable') from None


# ═══════════════════════════════════════════════════════════════════════════
# FONT DOWNLOAD / CACHE
# ═══════════════════════════════════════════════════════════════════════════

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_LATIN_BLOCK_RE = re.compile(
    r"/\*\s*latin\s*\*/\s*@font-face\s*\{([^}]+)\}",
    re.DOTALL,
)
_URL_RE = re.compile(r"url\((https://[^)]+\.woff2)\)")
_WEIGHT_RE = re.compile(r"font-weight:\s*(\d+)")


def ensure_font(family: str, weights: Optional[list[int]] = None) -> bool:
    """Download a Google Font to the cache if not already bundled/cached.

    Returns True if font is now available offline, False on failure.
    """
    if family in _manifest:
        return True  # Already bundled

    dirname = _safe_dirname(family)
    cache_dir = _CACHE_DIR / dirname
    if cache_dir.exists() and list(cache_dir.glob("*.woff2")):
        return True  # Already cached

    if weights is None:
        weights = [300, 400, 600, 700]

    try:
        weight_str = ";".join(str(w) for w in sorted(weights))
        encoded = family.replace(" ", "+")
        url = (
            f"https://fonts.googleapis.com/css2?"
            f"family={encoded}:wght@{weight_str}&display=swap"
        )
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            css = resp.read().decode("utf-8")
    except Exception as exc:
        logger.warning("Failed to fetch CSS for '%s': %s", family, exc)
        return False

    blocks = _LATIN_BLOCK_RE.findall(css)
    if not blocks:
        logger.warning("No latin @font-face found for '%s'", family)
        return False

    cache_dir.mkdir(parents=True, exist_ok=True)
    downloaded = set()
    for block in blocks:
        url_m = _URL_RE.search(block)
        weight_m = _WEIGHT_RE.search(block)
        if not url_m or not weight_m:
            continue
        woff2_url = url_m.group(1)
        weight = weight_m.group(1)

        if woff2_url in downloaded:
            continue

        fname = f"{dirname}-{weight}.woff2"
        dest = cache_dir / fname
        if dest.exists():
            downloaded.add(woff2_url)
            continue

        try:
            req = urllib.request.Request(woff2_url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            dest.write_bytes(data)
            downloaded.add(woff2_url)
            logger.info("Cached font: %s (%d bytes)", fname, len(data))

            # For variable fonts, same URL serves all weights — copy for each
            for w in weights:
                w_dest = cache_dir / f"{dirname}-{w}.woff2"
                if not w_dest.exists():
                    w_dest.write_bytes(data)
        except Exception as exc:
            logger.warning("Failed to download %s: %s", fname, exc)

    return bool(list(cache_dir.glob("*.woff2")))


# ═══════════════════════════════════════════════════════════════════════════
# FONT LISTING
# ═══════════════════════════════════════════════════════════════════════════

def list_available_fonts() -> list[dict]:
    """Return all available fonts (bundled + cached).

    Returns list of:
        {name, source, category, weights, offline}
    """
    fonts = []

    # Bundled
    for family in _manifest:
        weights = [int(w) for w in _manifest[family].keys()]
        fonts.append({
            "name": family,
            "source": "bundled",
            "category": _CATEGORIES.get(family, "Sans"),
            "weights": sorted(weights),
            "offline": True,
        })

    # Cached (not already in bundled)
    bundled_names = set(_manifest.keys())
    if _CACHE_DIR.exists():
        for d in sorted(_CACHE_DIR.iterdir()):
            if d.is_dir() and list(d.glob("*.woff2")):
                # Derive family name from dir
                dirname = d.name
                # Check not already bundled
                is_bundled = False
                for fam in bundled_names:
                    if _safe_dirname(fam) == dirname:
                        is_bundled = True
                        break
                if is_bundled:
                    continue

                weight_re = re.compile(r"-(\d+)\.woff2$")
                weights = []
                for f in d.glob("*.woff2"):
                    m = weight_re.search(f.name)
                    if m:
                        weights.append(int(m.group(1)))

                # Guess family name from dirname
                name = dirname.replace("-", " ").title()
                fonts.append({
                    "name": name,
                    "source": "cached",
                    "category": "Sans",  # unknown for cached
                    "weights": sorted(set(weights)),
                    "offline": True,
                })

    return fonts


def is_font_available_offline(family: str) -> bool:
    """Check if a font is available without internet."""
    if family in _manifest:
        return True
    cache_dir = _CACHE_DIR / _safe_dirname(family)
    return cache_dir.exists() and bool(list(cache_dir.glob("*.woff2")))


def get_bundled_font_names() -> list[str]:
    """Return names of all 25 bundled fonts."""
    return list(_manifest.keys())
