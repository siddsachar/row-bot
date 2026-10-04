"""Explicit Hermes discovery and pinned, nonexecuting package inspection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from collections.abc import Callable
import json
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.parse import quote, urlsplit
from uuid import uuid4
import zipfile

import httpx

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.integrations.safe import TtlCache, fetch, write_atomic
from row_bot.package_files import check_package_tree, contained_path, relative_package_path
from row_bot.plugins.manifest import parse_manifest, ManifestError

CATALOG_URL = "https://hermes-agent.nousresearch.com/docs/api/plugin-catalog.json"
_CATALOG_MIRROR = "https://nousresearch.github.io/hermes-agent/docs/api/plugin-catalog.json"
_HOSTS = {"hermes-agent.nousresearch.com", "api.github.com", "codeload.github.com", "raw.githubusercontent.com"}
_LOCK = threading.RLock()
_PREVIEWS = TtlCache(1200, 64, full="Package preview capacity reached; finish or cancel a preview.")


def _public_bytes(url: str, *, maximum: int = 4 * 1024 * 1024) -> bytes:
    # Only the reviewed catalog migration may cross origins; downloads never redirect.
    return fetch(url, hosts=_HOSTS, max_bytes=maximum, exact_redirects={CATALOG_URL: _CATALOG_MIRROR})


def _catalog_document(value: object) -> dict:
    if (not isinstance(value, dict) or not isinstance(value.get("entries"), list)
            or len(value["entries"]) > 2000 or not isinstance(value.get("removed"), list)
            or len(value["removed"]) > 2000):
        raise ValueError("invalid_catalog")
    return value


def _cache_path() -> Path:
    return get_row_bot_data_dir(create=False) / "hermes_catalog_cache.json"


def read_catalog(*, refresh: bool = False, cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Cache-only by default; an unavailable source never erases cached results."""
    saved = {}
    path = _cache_path()
    if path.is_file() and not path.is_symlink() and path.stat().st_size <= 4 * 1024 * 1024:
        try:
            saved = _catalog_document(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    status, reason = ("cached", "") if saved else ("empty", "Refresh Hermes to load its public catalog.")
    if refresh:
        previous = saved
        try:
            value = _catalog_document(json.loads(_public_bytes(CATALOG_URL)))
            saved = {**value, "fetched_at": time.time()}
            if cancelled():
                raise ValueError("integration_search_cancelled")
            write_atomic(path, json.dumps(saved), cancelled=cancelled)
            status, reason = "live", ""
        except httpx.HTTPStatusError as exc:
            saved = previous
            status = "stale" if saved else "error"
            reason = "Hermes rate limited; retry after " + exc.response.headers.get("Retry-After", "the source allows it")[:80] if exc.response.status_code == 429 else "Hermes is unavailable; cached entries remain available."
        except (ValueError, OSError, httpx.HTTPError):
            saved = previous
            status, reason = ("stale" if saved else "error"), "Hermes is unavailable; cached entries remain available."
    entries = []
    for raw in saved.get("entries", []) if isinstance(saved, dict) else []:
        try:
            if not isinstance(raw, dict) or not re.fullmatch(r"[a-z0-9_-]{1,64}", raw.get("name", "")):
                continue
            repo, subdir, pin = repository_pin(raw["repo"], raw.get("subdir", ""), raw["sha"])
            removed = removed_reason(raw["name"], repo, saved)
            entries.append({"id": "hermes:" + raw["name"], "name": str(raw.get("title") or raw["name"])[:160],
                "description": str(raw.get("description", ""))[:1000], "source": "hermes", "publisher": str(raw.get("maintainer", ""))[:160],
                "url": repo, "source_identity": repo + ("#" + subdir if subdir else ""), "pin": pin, "subdirectory": subdir,
                "version": str(raw.get("version", ""))[:128], "platforms": raw.get("platforms", [])[:12],
                "compatibility": "unsupported" if removed else "not_inspected", "reason": removed or "Inspect the pinned package to check its portable components."})
        except (ValueError, KeyError, TypeError):
            continue
    return {"entries": entries, "removed": saved.get("removed", []), "status": status, "message": reason,
            "fetched_at": saved.get("fetched_at", 0)}


def repository_pin(repo: str, subdir: str, pin: str) -> tuple[str, str, str]:
    parsed = urlsplit(repo)
    path = parsed.path.removesuffix(".git").strip("/")
    if (parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in {None, 443}
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path)
            or not re.fullmatch(r"[a-fA-F0-9]{40}", pin)):
        raise ValueError("An exact public GitHub repository and 40-character commit are required.")
    if subdir:
        relative_package_path(subdir)
    return "https://github.com/" + path, subdir, pin.lower()


def removed_reason(name: str, repo: str, catalog: dict | None = None) -> str:
    catalog = catalog if catalog is not None else read_catalog()
    for entry in catalog.get("removed", []):
        if isinstance(entry, dict) and (entry.get("name") == name or str(entry.get("repo", "")).removesuffix(".git").rstrip("/").casefold() == repo.casefold()):
            return "Removed upstream: " + str(entry.get("reason") or "The publisher withdrew this entry.")[:300]
    return ""


@dataclass
class PackagePreview:
    preview_id: str
    plugin_id: str
    owner_id: str
    source_identity: str
    pin: str
    digest: str
    archive_digest: str
    root: Path
    summary: dict
    catalog_reference: str = ""


def inspect_package(*, owner_id: str, reference: str, local: bool = False) -> dict:
    """Resolve moving refs once, or inspect an authorized local folder/archive."""
    from row_bot.plugins.installer import _safe_extract_zip
    from row_bot.plugins.devtools import compute_plugin_checksum

    pin = subdir = archive_digest = ""
    if reference.startswith("hermes:"):
        catalog = read_catalog()
        entry = next((e for e in catalog["entries"] if e["id"] == reference), None)
        if entry is None or entry["compatibility"] == "unsupported":
            raise ValueError("Hermes entry is missing or removed; refresh the source.")
        repo, subdir, pin = repository_pin(entry["url"], entry["subdirectory"], entry["pin"])
        source_identity = entry["source_identity"]
    elif reference.startswith("https://github.com/"):
        parsed = urlsplit(reference)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2 or parsed.query or parsed.username or parsed.password:
            raise ValueError("Unsupported repository reference")
        repo = "https://github.com/" + "/".join(parts[:2]).removesuffix(".git")
        ref = parsed.fragment or (parts[3] if len(parts) >= 4 and parts[2] == "tree" else "HEAD")
        subdir = "/".join(parts[4:]) if len(parts) >= 4 else ""
        repository_pin(repo, subdir, "0" * 40)
        if not re.fullmatch(r"[a-fA-F0-9]{40}", ref):
            resolved = json.loads(_public_bytes("https://api.github.com/repos/" + "/".join(parts[:2]) + "/commits/" + quote(ref, safe="")))
            ref = resolved.get("sha", "")
        repo, subdir, pin = repository_pin(repo, subdir, ref)
        source_identity = repo + ("#" + subdir if subdir else "")
        if removed_reason("", repo):
            raise ValueError("Repository removed upstream")
    elif reference == "bundled:local-text-tools":
        source = Path(__file__).parent / "bundled" / "local-text-tools"
        source_identity, repo = "row-bot:local-text-tools", ""
    elif local:
        source = Path(reference).resolve(strict=True)
        source_identity = "local:" + str(source)
        repo = ""
    else:
        raise ValueError("Use a catalog entry, GitHub package, or authorized local import.")
    preview_id = uuid4().hex
    stage = contained_path(get_row_bot_data_dir(create=False), "plugin_previews/" + preview_id)
    with _LOCK:
        _PREVIEWS.room()
        stage.mkdir(parents=True)
    root = stage / "package"
    try:
        if repo:
            data = _public_bytes("https://codeload.github.com/" + repo.split("github.com/", 1)[1] + "/zip/" + pin, maximum=64 * 1024 * 1024)
            archive_digest = "sha256:" + hashlib.sha256(data).hexdigest()
            archive = stage / "source.zip"
            archive.write_bytes(data)
        elif source.is_file():
            if source.stat().st_size > 64 * 1024 * 1024:
                raise ValueError("Archive too large")
            archive = source
            archive_digest = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
        else:
            check_package_tree(source)
            shutil.copytree(source, root, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
            archive = None
        if archive:
            with zipfile.ZipFile(archive) as zipped:
                _safe_extract_zip(zipped, stage / "extracted")
            extracted = stage / "extracted"
            tops = list(extracted.iterdir())
            base = tops[0] if len(tops) == 1 and tops[0].is_dir() else extracted
            package_root = contained_path(base, subdir) if subdir else base
            shutil.copytree(package_root, root)
        check_package_tree(root)
        manifest = parse_manifest(root, source_identity=source_identity)
        digest = compute_plugin_checksum(root)
        summary = {"preview_id": preview_id, "plugin_id": manifest.id, "name": manifest.name, "description": manifest.description[:1000],
            "version": manifest.version, "license": manifest.license, "publisher": manifest.author.name,
            "format": manifest.package_format, "compatibility": "partial" if manifest.diagnostics else "supported",
            "diagnostics": manifest.diagnostics[:128], "source": repo or ("Bundled Row-Bot example" if reference.startswith("bundled:") else "Authorized local package"), "pin": pin,
            "tree_digest": digest, "archive_digest": archive_digest, "adapter_version": 1,
            "skills": [{"name": s.get("display_name") or s["name"], "description": str(s.get("description", ""))[:1024]} for s in manifest.provides.skills],
            "servers": [{"key": s["id"], "transport": s["transport"], "command": s.get("command", ""), "args": s.get("args", []), "url": s.get("url", "")} for s in manifest.provides.mcp_servers],
            "permissions": manifest.permissions, "evidence": "Format inspected; live service and platform behavior not tested."}
        _PREVIEWS.put((owner_id, preview_id), PackagePreview(preview_id, manifest.id, owner_id, source_identity, pin, digest,
            archive_digest, root, summary, reference if reference.startswith("hermes:") else ""))
        return summary
    except (ManifestError, FileNotFoundError) as exc:
        if not (root / "plugin.json").is_file() and ((root / "plugin.yaml").exists() or (root / "__init__.py").exists()):
            raise ValueError("Native Hermes packages require their host and are not supported. No package code was executed.") from None
        raise ValueError("Package format is unsupported or invalid: " + str(exc)[:200]) from None


def get_preview(owner_id: str, preview_id: str) -> PackagePreview:
    preview = _PREVIEWS.get((owner_id, preview_id))
    if preview is None:
        raise ValueError("package_preview_expired")
    if preview.catalog_reference:
        current = read_catalog(refresh=True)
        entry = next((row for row in current["entries"] if row["id"] == preview.catalog_reference), None)
        if current["status"] != "live" or entry is None or entry["compatibility"] == "unsupported":
            raise ValueError("package_source_removed_or_unavailable")
        if entry["pin"] != preview.pin or entry["source_identity"] != preview.source_identity:
            raise ValueError("package_source_changed")
    if preview.source_identity.startswith("https://github.com/") and removed_reason("", preview.source_identity.split("#", 1)[0]):
        raise ValueError("package_source_removed")
    from row_bot.plugins.devtools import compute_plugin_checksum
    check_package_tree(preview.root)
    if compute_plugin_checksum(preview.root) != preview.digest:
        raise ValueError("package_preview_changed")
    return preview


def activation_block(record: dict) -> str:
    """Known source withdrawals forbid new activation without hiding local data."""
    source = str(record.get("package", {}).get("source_identity", ""))
    if not source.startswith("https://github.com/"):
        return ""
    return removed_reason("", source.split("#", 1)[0])


def inspect_marketplace_update(*, owner_id: str, plugin_id: str, origin,
        source_identity: str, checksum: str, source_revision: str) -> PackagePreview:
    """Reuse the existing owner-bound preview cache for checksum-verified updates."""
    from row_bot.plugins import installer
    from row_bot.plugins.devtools import compute_plugin_checksum
    key = hashlib.sha256(json.dumps([str(get_row_bot_data_dir(create=False)), plugin_id, source_identity, checksum, source_revision]).encode()).hexdigest()[:32]
    try:
        return get_preview(owner_id, key)
    except ValueError:
        pass
    _PREVIEWS.room()
    base = get_row_bot_data_dir() / "plugin_previews"
    base.mkdir(parents=True, exist_ok=True)
    root = base / uuid4().hex / "package"
    root.parent.mkdir()
    manifest = installer._prepare_package(plugin_id, root, source_dir=origin.local_dir,
        source_ref=source_identity, archive_url=origin.archive_url, archive_path=origin.archive_path,
        expected_checksum=checksum or None)
    preview = PackagePreview(key, plugin_id, owner_id, source_identity, "", compute_plugin_checksum(root), "", root,
        {"format": manifest.package_format, "source_kind": "marketplace"})
    _PREVIEWS.put((owner_id, key), preview)
    return preview
