"""Explicit Hermes discovery and pinned, nonexecuting package inspection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
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
from row_bot.package_files import check_package_tree, contained_path, relative_package_path
from row_bot.plugins.manifest import parse_manifest, ManifestError

CATALOG_URL = "https://hermes-agent.nousresearch.com/docs/api/plugin-catalog.json"
_LOCK = threading.RLock()
_PREVIEWS: dict[tuple[str, str], "PackagePreview"] = {}
_TTL = 1200


def _public_bytes(url: str, *, maximum: int = 4 * 1024 * 1024) -> bytes:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {
            "hermes-agent.nousresearch.com", "api.github.com", "codeload.github.com", "raw.githubusercontent.com"}
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise ValueError("package_source_not_supported")
    # No cookies/credentials and no redirects to unreviewed origins.
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", url, headers={"User-Agent": "Row-Bot-Integrations"}) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > maximum:
                    raise ValueError("package_download_too_large")
    return bytes(data)


def _cache_path() -> Path:
    return get_row_bot_data_dir(create=False) / "hermes_catalog_cache.json"


def read_catalog(*, refresh: bool = False) -> dict:
    """Cache-only by default; an unavailable source never erases cached results."""
    saved = {}
    path = _cache_path()
    if path.is_file() and not path.is_symlink() and path.stat().st_size <= 4 * 1024 * 1024:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    status, reason = ("cached", "") if saved else ("empty", "Refresh Hermes to load its public catalog.")
    if refresh:
        try:
            value = json.loads(_public_bytes(CATALOG_URL))
            if (not isinstance(value, dict) or not isinstance(value.get("entries"), list)
                    or len(value["entries"]) > 2000 or not isinstance(value.get("removed"), list)):
                raise ValueError("invalid_catalog")
            saved = {**value, "fetched_at": time.time()}
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(saved), encoding="utf-8")
            temporary.replace(path)
            status, reason = "live", ""
        except httpx.HTTPStatusError as exc:
            status = "stale" if saved else "error"
            reason = "Hermes rate limited; retry after " + exc.response.headers.get("Retry-After", "the source allows it")[:80] if exc.response.status_code == 429 else "Hermes is unavailable; cached entries remain available."
        except (ValueError, OSError, httpx.HTTPError):
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
    created: float


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
        for key, preview in list(_PREVIEWS.items()):
            if time.monotonic() - preview.created > _TTL:
                _PREVIEWS.pop(key)
        if len(_PREVIEWS) >= 64:
            raise ValueError("Package preview capacity reached; finish or cancel a preview.")
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
        with _LOCK:
            _PREVIEWS[(owner_id, preview_id)] = PackagePreview(preview_id, manifest.id, owner_id, source_identity, pin, digest, archive_digest, root, summary, time.monotonic())
        return summary
    except (ManifestError, FileNotFoundError) as exc:
        if not (root / "plugin.json").is_file() and ((root / "plugin.yaml").exists() or (root / "__init__.py").exists()):
            raise ValueError("Native Hermes packages require their host and are not supported. No package code was executed.") from None
        raise ValueError("Package format is unsupported or invalid: " + str(exc)[:200]) from None


def get_preview(owner_id: str, preview_id: str) -> PackagePreview:
    with _LOCK:
        preview = _PREVIEWS.get((owner_id, preview_id))
    if preview is None or time.monotonic() - preview.created > _TTL:
        raise ValueError("package_preview_expired")
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
