"""Designer — brand extraction, presets, and brand management utilities."""

from __future__ import annotations

import json
import logging
import re
import os
import stat
import hashlib
from contextlib import ExitStack
from collections.abc import Callable
from uuid import UUID
from itertools import islice
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

def save_brand_preset(name: str, brand: BrandConfig, *, strict: bool = False,
                      expected_digest: str | None = None, command_id: str | None = None,
                      validate: Callable[[], None] | None = None,
                      checkpoint: Callable | None = None) -> Path:
    """Save a brand config as a named preset."""
    if strict:
        content = json.dumps({'name': name, **brand.to_dict()}, indent=2).encode('utf-8')
        return _mutate_preset(name, content, expected_digest, command_id, validate, checkpoint)
    _BRAND_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)[:50]
    path = _BRAND_DIR / f"{safe}.json"
    path.write_text(json.dumps({"name": name, **brand.to_dict()}, indent=2))
    logger.info("Saved brand preset '%s' to %s", name, path)
    return path


def load_brand_presets(*, strict: bool = False) -> dict[str, BrandConfig]:
    """Return all custom brand presets from disk."""
    result: dict[str, BrandConfig] = {}
    if not _BRAND_DIR.exists():
        return result
    if strict:
        if any(path.is_symlink() or path.is_junction() for path in (_BRAND_DIR, *_BRAND_DIR.parents)):
            raise ValueError('brand_catalog_unavailable')
        paths = list(islice(_BRAND_DIR.iterdir(), 1025))
        if len(paths) > 1024:
            raise ValueError('brand_catalog_too_large')
        paths = sorted(path for path in paths if path.suffix == '.json')
    else:
        paths = sorted(_BRAND_DIR.glob("*.json"))
    total = 0
    for p in paths:
        try:
            if strict:
                parent = p.parent.stat()
                before = p.lstat()
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 65536:
                    raise ValueError('brand_catalog_unavailable')
                with p.open('rb') as handle:
                    opened = os.fstat(handle.fileno())
                    if not os.path.samestat(before, opened):
                        raise ValueError('brand_catalog_unavailable')
                    raw = handle.read(65537)
                    finished = os.fstat(handle.fileno())
                named = p.lstat()
                if (len(raw) > 65536 or not os.path.samestat(opened, named)
                        or not os.path.samestat(parent, p.parent.stat())
                        or (opened.st_size, opened.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)
                        or (named.st_size, named.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)):
                    raise ValueError('brand_catalog_unavailable')
                total += len(raw)
                if total > 16 * 1024 * 1024:
                    raise ValueError('brand_catalog_too_large')
                data = json.loads(raw)
                if not isinstance(data, dict) or not isinstance(data.get('name', p.stem), str) or len(data.get('name', p.stem)) > 256:
                    raise ValueError('brand_catalog_unavailable')
            else:
                data = json.loads(p.read_text())
            name = data.pop("name", p.stem)
            result[name] = BrandConfig.from_dict(data)
        except Exception:
            if strict:
                raise ValueError('brand_catalog_unavailable') from None
            logger.warning("Skipping invalid brand preset: %s", p)
    return result


def delete_brand_preset(name: str, *, strict: bool = False,
                        expected_digest: str | None = None, command_id: str | None = None,
                        validate: Callable[[], None] | None = None,
                        checkpoint: Callable | None = None) -> bool:
    """Delete a custom preset by name."""
    if strict:
        _mutate_preset(name, None, expected_digest, command_id, validate, checkpoint)
        return True
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)[:50]
    path = _BRAND_DIR / f"{safe}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def _preset_filename(name: str) -> str:
    if (not isinstance(name, str) or not name.strip() or len(name) > 256
            or any(ord(char) < 32 for char in name)):
        raise ValueError('invalid_brand_name')
    return ''.join(c if c.isalnum() or c in ' -_' else '_' for c in name)[:50] + '.json'


def read_preset_revision(name: str) -> str:
    """Exact bytes for an explicit global preset review; no writes or adoption."""
    from row_bot.developer.edits import read_edit_bytes
    path = _BRAND_DIR / _preset_filename(name)
    if not _BRAND_DIR.exists():
        return 'missing'
    data, digest, _identity, _mode = read_edit_bytes(_BRAND_DIR, path.name)
    if data is not None and (len(data) > 65536 or json.loads(data).get('name') != name):
        raise ValueError('brand_name_conflict')
    return digest


def _mutate_preset(name, data, expected_digest, command_id, validate, checkpoint):
    """Single-link publication/retirement over the canonical preset directory.

    The existing command owner retains FileEditRecovery privately before any
    original moves. No original/candidate is overwritten or automatically erased.
    A failed attempt must be inspected through that receipt, never blindly replayed.
    """
    from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
    from row_bot.developer.edits import FileEditError, FileEditRecovery, _rename_edit_no_replace, file_edit_metadata_digest
    filename = _preset_filename(name)
    try:
        if (str(UUID(command_id)) != command_id or not callable(validate) or not callable(checkpoint)
                or not isinstance(expected_digest, str)
                or expected_digest != 'missing' and not re.fullmatch('[0-9a-f]{64}', expected_digest)):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise FileEditError('invalid_edit') from None
    if data is not None and len(data) > 65536:
        raise FileEditError('file_too_large')
    validate()
    with ExitStack() as stack:
        parent = stack.enter_context(_empty_parent_guard(_BRAND_DIR.parent, _directory_identity(_BRAND_DIR.parent, parent=True)))
        if parent is None:
            _BRAND_DIR.mkdir(exist_ok=True)
        else:
            try:
                os.mkdir(_BRAND_DIR.name, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
        root = stack.enter_context(_empty_parent_guard(_BRAND_DIR, _directory_identity(_BRAND_DIR, parent=True)))
        root_identity = _BRAND_DIR.stat()
        def directory(base, base_path, child, *, exclusive=False):
            path = base_path / child
            if base is None:
                path.mkdir(exist_ok=not exclusive)
                stack.enter_context(_empty_parent_guard(path, _directory_identity(path, parent=True)))
                return None, path
            try:
                os.mkdir(child, mode=0o700, dir_fd=base)
            except FileExistsError:
                if exclusive:
                    raise
            fd = os.open(child, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base)
            stack.callback(os.close, fd)
            return fd, path
        def read(directory_fd, directory_path, leaf):
            path = directory_path / leaf
            try:
                before = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False) if directory_fd is not None else path.lstat()
            except FileNotFoundError:
                return None, 'missing', '', 0o600, ''
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 65536:
                raise FileEditError('file_metadata_unavailable')
            fd = os.open(leaf if directory_fd is not None else path,
                         os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory_fd)
            with os.fdopen(fd, 'rb') as handle:
                opened = os.fstat(handle.fileno())
                value = handle.read(65537)
                metadata = file_edit_metadata_digest(handle.fileno() if directory_fd is not None else path)
                finished = os.fstat(handle.fileno())
            named = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False) if directory_fd is not None else path.lstat()
            if (not os.path.samestat(before, opened) or not os.path.samestat(opened, named)
                    or len(value) > 65536 or opened.st_nlink != 1 or named.st_nlink != 1
                    or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (finished.st_size, finished.st_mtime_ns, finished.st_ctime_ns)
                    or (named.st_size, named.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)):
                raise FileEditError('file_revision_conflict')
            return value, hashlib.sha256(value).hexdigest(), f'{opened.st_dev}:{opened.st_ino}', stat.S_IMODE(opened.st_mode), metadata
        def rename(source_fd, source_path, source, dest_fd, dest_path, destination):
            _rename_edit_no_replace(source if source_fd is not None else source_path / source,
                destination if dest_fd is not None else dest_path / destination,
                src_dir_fd=source_fd, dst_dir_fd=dest_fd)
        original = read(root, _BRAND_DIR, filename)
        if original[1] != expected_digest:
            raise FileEditError('file_revision_conflict')
        if original[0] is not None and json.loads(original[0]).get('name') != name:
            raise FileEditError('file_revision_conflict')
        if original[0] == data:
            return _BRAND_DIR / filename
        recovery_root, recovery_path = directory(root, _BRAND_DIR, '.row-bot-edit-recovery')
        attempt, attempt_path = directory(recovery_root, recovery_path, command_id, exclusive=True)
        candidate = (None, 'missing', '', 0o600, '')
        if data is not None:
            fd = os.open('candidate' if attempt is not None else attempt_path / 'candidate',
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), original[3], dir_fd=attempt)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                if attempt is not None:
                    os.fchmod(handle.fileno(), original[3])
            if attempt is None:
                os.chmod(attempt_path / 'candidate', original[3])
            candidate = read(attempt, attempt_path, 'candidate')
            if original[0] is not None and original[4] != candidate[4]:
                raise FileEditError('file_metadata_unavailable')
        recovery = FileEditRecovery(command_id, filename, f'{root_identity.st_dev}:{root_identity.st_ino}',
            original[1], candidate[1], original[2], candidate[2], candidate[4] or original[4])
        checkpoint(recovery)
        try:
            validate()
            if not os.path.samestat(root_identity, _BRAND_DIR.lstat()):
                raise FileEditError('file_revision_conflict', recovery)
            if read(root, _BRAND_DIR, filename) != original:
                raise FileEditError('file_revision_conflict', recovery)
            if original[0] is not None:
                rename(root, _BRAND_DIR, filename, attempt, attempt_path, 'previous')
                if read(attempt, attempt_path, 'previous') != original:
                    raise FileEditError('file_revision_conflict', recovery)
            validate()
            if data is not None:
                if read(attempt, attempt_path, 'candidate') != candidate:
                    raise FileEditError('file_revision_conflict', recovery)
                rename(attempt, attempt_path, 'candidate', root, _BRAND_DIR, filename)
            if read(root, _BRAND_DIR, filename) != candidate or not os.path.samestat(root_identity, _BRAND_DIR.lstat()):
                raise FileEditError('file_revision_conflict', recovery)
        except Exception:
            if read(root, _BRAND_DIR, filename)[0] is None and read(attempt, attempt_path, 'previous')[0] is not None:
                try:
                    rename(attempt, attempt_path, 'previous', root, _BRAND_DIR, filename)
                except OSError:
                    pass
            raise
        return _BRAND_DIR / filename


def get_all_presets(*, strict: bool = False) -> dict[str, BrandConfig]:
    """Built-in + custom presets merged (custom can override built-in names)."""
    merged = dict(BRAND_PRESETS)
    merged.update(load_brand_presets(strict=strict))
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
