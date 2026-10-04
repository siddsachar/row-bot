"""App icons: bundled marks for featured apps, letter avatars, and Registry rasters.

Showing an icon reads only local data. Registry icons are downloaded only during
a catalog update the user asked for (or scheduled): raster only, size-capped,
decoded and re-encoded locally as a small PNG. Remote SVG is never accepted.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import cache
import hashlib
from html import escape
import io
import json
from pathlib import Path
import re
import time

from row_bot.integrations.safe import write_atomic

SIZE = 64
MAX_BYTES = 256 * 1024
MAX_SIDE = 2048
PER_UPDATE = 200
CACHE_BYTES = 20 * 1024 * 1024
_PATH = re.compile(r"[MmLlHhVvCcSsQqTtAaZz0-9.,eE\s+-]{1,40000}")
_CACHED = re.compile(r"cached:[0-9a-f]{32}")
_PALETTE = ("#4F46E5", "#0E7490", "#B45309", "#047857", "#BE185D", "#6D28D9", "#1D4ED8", "#B91C1C")
_SEEN: dict = {"key": None, "names": frozenset()}


@cache
def marks() -> dict[str, dict]:
    """Bundled marks by slug, each with its licence and source."""
    raw = json.loads(Path(__file__).with_name("icons.json").read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("invalid_icon_catalog")
    for slug, mark in raw["icons"].items():
        if (not re.fullmatch(r"[a-z0-9]{1,64}", slug) or not _PATH.fullmatch(mark["path"])
                or not re.fullmatch(r"[0-9A-F]{6}", mark["hex"]) or not mark["license"] or not mark["source"].startswith("https://")):
            raise ValueError("invalid_icon_catalog: " + slug)
    return raw["icons"]


def license_of(icon: str) -> dict | None:
    """The licence a bundled mark is shipped under, or None for letters and cached rasters."""
    mark = marks().get(icon.removeprefix("si:")) if icon.startswith("si:") else None
    return {key: mark[key] for key in ("title", "license", "source", "guidelines") if key in mark} if mark else None


def folder(*, create: bool = False) -> Path:
    from row_bot.integrations.index import folder as catalogs
    path = catalogs(create=create) / "icons"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def remote_id(url: str) -> str:
    return "cached:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _cached() -> frozenset[str]:
    """Names of the cached rasters, reread only when the folder changes."""
    path = folder()
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return frozenset()
    if _SEEN["key"] != key:
        _SEEN.update(key=key, names=frozenset("cached:" + item.stem for item in path.glob("*.png")))
    return _SEEN["names"]


def entry_icon(app, remote_url: str, name: str) -> str:
    """The app's mark, else a cached Registry raster, else a letter avatar. Never fetches."""
    from row_bot.integrations.apps import letter
    if app is not None and app.icon:
        return app.icon
    if remote_url and remote_id(remote_url) in _cached():
        return remote_id(remote_url)
    return letter(app.name if app is not None else name)


def render(icon: str) -> tuple[bytes, str]:
    """Icon bytes and media type from local data only."""
    if icon.startswith("si:") and icon[3:] in marks():
        mark = marks()[icon[3:]]
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" role="img"><title>{escape(mark["title"])}</title>'
               f'<path fill="#{mark["hex"]}" d="{mark["path"]}"/></svg>')
        return svg.encode("utf-8"), "image/svg+xml"
    if re.fullmatch(r"letter:[A-Z0-9]", icon):
        color = _PALETTE[ord(icon[-1]) % len(_PALETTE)]
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img"><rect width="64" height="64" rx="14" '
               f'fill="{color}"/><text x="32" y="43" text-anchor="middle" font-family="system-ui,sans-serif" font-size="30" '
               f'font-weight="600" fill="#FFFFFF">{icon[-1]}</text></svg>')
        return svg.encode("utf-8"), "image/svg+xml"
    if _CACHED.fullmatch(icon):
        path = folder() / (icon[7:] + ".png")
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_BYTES:
            return path.read_bytes(), "image/png"
    raise ValueError("not_found")


def reencode(data: bytes) -> bytes:
    """Decode one raster image and re-encode it as a small PNG without metadata.

    SVG, HTML and every non-raster format are refused, as are oversized images."""
    from PIL import Image
    if len(data) > MAX_BYTES or data.lstrip()[:1] == b"<":
        raise ValueError("icon_refused")
    try:
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP", "GIF"]) as image:
            if image.width < 1 or image.height < 1 or image.width > MAX_SIDE or image.height > MAX_SIDE:
                raise ValueError("icon_refused")
            image.seek(0)
            frame = image.convert("RGBA")
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("icon_refused") from exc
    frame.thumbnail((SIZE, SIZE))
    out = io.BytesIO()
    frame.save(out, "PNG", optimize=True)
    return out.getvalue()


def _download(url: str) -> bytes:
    from row_bot.integrations.safe import fetch
    return fetch(url, hosts=None, max_bytes=MAX_BYTES, timeout=10, redirects=2, headers={"Accept": "image/png,image/jpeg,image/webp,image/gif"},
                 refused="icon_refused", too_large="icon_refused")


def cache_remote(urls: Iterable[str], *, cancelled: Callable[[], bool] = lambda: False,
                 download: Callable[[str], bytes] = _download, pause: float = 0.1, deadline: float = 180) -> dict:
    """Download, check and cache up to ``PER_UPDATE`` new raster icons. Only an update calls this."""
    cached = failed = 0
    stop = time.monotonic() + deadline
    target = folder(create=True)
    used = sum(item.stat().st_size for item in target.glob("*.png"))
    for url in dict.fromkeys(urls):
        if cached + failed >= PER_UPDATE or used >= CACHE_BYTES or cancelled() or time.monotonic() > stop:
            break
        path = target / (remote_id(url)[7:] + ".png")
        if not url.startswith("https://") or path.exists():
            continue
        try:
            data = reencode(download(url))
            write_atomic(path, data)
            used += len(data)
            cached += 1
        except Exception:  # One bad icon never stops an update; it keeps its letter avatar.
            failed += 1
        time.sleep(pause)
    return {"cached": cached, "failed": failed}
