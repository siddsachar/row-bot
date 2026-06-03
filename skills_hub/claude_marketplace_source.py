"""Claude-style marketplace manifest source."""

from __future__ import annotations

import urllib.parse
from typing import Any

from .github_source import GitHubInstallRef, GitHubSource, github_web_url, parse_github_install_ref
from .models import SourceResult, SkillBundle, SkillHubEntry
from .search_index import search_entries
from .sources import SkillSource, fetch_json, normalize_bundle_path, slugify, title_from_slug

MANIFEST_PATH = ".claude-plugin/marketplace.json"
CLAUDE_MARKETPLACE_ROOTS = [
    ("openai", "skills"),
    ("anthropics", "skills"),
]


class ClaudeMarketplaceSource(SkillSource):
    id = "claude_marketplace"
    display_name = "GitHub Manifest"
    source_group = "github"
    trust_default = "community"
    supports_browse = False
    supports_search = False
    supports_import = True

    def browse(self, limit: int = 50, cursor: str | None = None) -> SourceResult:
        entries: list[SkillHubEntry] = []
        errors: list[str] = []
        for owner, repo in CLAUDE_MARKETPLACE_ROOTS:
            if len(entries) >= limit:
                break
            try:
                entries.extend(self._entries_from_repo(owner, repo)[: max(1, limit - len(entries))])
            except Exception as exc:
                errors.append(f"{owner}/{repo}: {exc}")
        status = "live" if entries else ("error" if errors else "empty")
        return SourceResult(entries, self.id, status, "; ".join(errors))

    def search(self, query: str, limit: int = 24) -> list[SkillHubEntry]:
        parsed = parse_github_install_ref(query)
        if parsed is not None:
            try:
                return self._entries_from_repo(parsed.owner, parsed.repo, ref=parsed.ref)[:limit]
            except Exception:
                return []
        return search_entries(self.browse(limit=max(limit, 100)).entries, query, limit=limit)

    def can_resolve(self, value: str) -> bool:
        parsed = parse_github_install_ref(value)
        return parsed is not None

    def resolve(self, value: str) -> SourceResult:
        parsed = parse_github_install_ref(value)
        if parsed is None:
            return SourceResult([], self.id, "empty", "Input is not a Claude-style GitHub source.")
        try:
            entries = self._entries_from_repo(parsed.owner, parsed.repo, ref=parsed.ref)
        except Exception as exc:
            return SourceResult([], self.id, "error", str(exc))
        return SourceResult(entries, self.id, "live" if entries else "empty")

    def inspect(self, entry: SkillHubEntry) -> SkillBundle:
        return GitHubSource().fetch(entry.install_ref)

    def fetch(self, install_ref: str) -> SkillBundle:
        return GitHubSource().fetch(install_ref)

    def _entries_from_repo(self, owner: str, repo: str, *, ref: str = "") -> list[SkillHubEntry]:
        url = _raw_github_url(owner, repo, ref or "main", MANIFEST_PATH)
        try:
            data = fetch_json(url, headers=GitHubSource()._headers())
        except Exception:
            if ref:
                raise
            data = fetch_json(_raw_github_url(owner, repo, "master", MANIFEST_PATH), headers=GitHubSource()._headers())
            ref = "master"
        return parse_claude_marketplace_manifest(data, owner=owner, repo=repo, ref=ref or "main")


def parse_claude_marketplace_manifest(
    data: Any,
    *,
    owner: str,
    repo: str,
    ref: str = "main",
) -> list[SkillHubEntry]:
    entries: list[SkillHubEntry] = []
    if isinstance(data, dict):
        raw_items = data.get("skills") or data.get("entries") or data.get("items") or []
        publisher = data.get("publisher") or data.get("author") or f"{owner}/{repo}"
        entries.extend(
            entry
            for entry in _entries_from_manifest_items(
                raw_items,
                owner=owner,
                repo=repo,
                ref=ref,
                publisher=str(publisher),
                plugin_name="",
                plugin_root="",
            )
            if entry is not None
        )
        plugins = data.get("plugins") if isinstance(data.get("plugins"), list) else []
        for plugin in plugins:
            if not isinstance(plugin, dict):
                continue
            plugin_name = str(plugin.get("name") or plugin.get("title") or plugin.get("id") or "").strip()
            plugin_publisher = str(plugin.get("publisher") or plugin.get("author") or publisher)
            plugin_root = str(plugin.get("path") or plugin.get("root") or plugin.get("directory") or "").strip()
            plugin_items = plugin.get("skills") or plugin.get("entries") or plugin.get("items") or []
            entries.extend(
                entry
                for entry in _entries_from_manifest_items(
                    plugin_items,
                    owner=owner,
                    repo=repo,
                    ref=ref,
                    publisher=plugin_publisher,
                    plugin_name=plugin_name,
                    plugin_root=plugin_root,
                )
                if entry is not None
            )
    elif isinstance(data, list):
        raw_items = data
        publisher = f"{owner}/{repo}"
        entries.extend(
            entry
            for entry in _entries_from_manifest_items(
                raw_items,
                owner=owner,
                repo=repo,
                ref=ref,
                publisher=str(publisher),
                plugin_name="",
                plugin_root="",
            )
            if entry is not None
        )
    else:
        return []
    return entries


def _entries_from_manifest_items(
    raw_items: Any,
    *,
    owner: str,
    repo: str,
    ref: str,
    publisher: str,
    plugin_name: str,
    plugin_root: str,
) -> list[SkillHubEntry | None]:
    if not isinstance(raw_items, list):
        return []
    entries: list[SkillHubEntry | None] = []
    for raw in raw_items:
        if isinstance(raw, str):
            path = _normalize_manifest_path(raw, plugin_root=plugin_root)
            raw = {"path": path, "name": title_from_slug(path.rstrip("/").split("/")[-1])}
        if not isinstance(raw, dict):
            continue
        entries.append(_entry_from_manifest_item(
            raw,
            owner=owner,
            repo=repo,
            ref=ref,
            publisher=publisher,
            plugin_name=plugin_name,
            plugin_root=plugin_root,
        ))
    return entries


def _entry_from_manifest_item(
    raw: dict[str, Any],
    *,
    owner: str,
    repo: str,
    ref: str,
    publisher: str,
    plugin_name: str = "",
    plugin_root: str = "",
) -> SkillHubEntry | None:
    name = str(raw.get("name") or raw.get("title") or raw.get("id") or "").strip()
    path = _normalize_manifest_path(
        str(raw.get("path") or raw.get("skill_path") or raw.get("directory") or "").strip(),
        plugin_root=plugin_root,
    )
    if not name or not path:
        return None
    install_ref = GitHubInstallRef(owner, repo, path, ref).format()
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    entry_id = str(raw.get("id") or slugify(name))
    return SkillHubEntry(
        id=f"github_manifest:{owner}/{repo}:{entry_id}",
        name=name,
        description=str(raw.get("description") or raw.get("summary") or ""),
        source="github",
        source_id=f"{owner}/{repo}",
        install_ref=install_ref,
        url=str(raw.get("url") or github_web_url(owner, repo, path, ref)),
        author=str(raw.get("author") or publisher),
        tags=[str(tag) for tag in tags],
        trust_level="community",
        metadata={
            "repository": f"{owner}/{repo}",
            "path": path,
            "ref": ref,
            "manifest": MANIFEST_PATH,
            "publisher": publisher,
            "plugin": plugin_name,
            "source_adapter": "claude_marketplace",
            "manifest_badge": "Claude manifest",
            "trust_level": "community",
        },
    )


def _normalize_manifest_path(path: str, *, plugin_root: str = "") -> str:
    clean = str(path or "").replace("\\", "/").strip()
    while clean.startswith("./"):
        clean = clean[2:]
    clean = clean.lstrip("/")
    base = normalize_bundle_path(plugin_root)
    if base and clean and not clean.startswith((
        "skills/",
        ".claude/skills/",
        ".agents/skills/",
        "agents/skills/",
    )):
        clean = f"{base}/{clean}"
    return normalize_bundle_path(clean)


def _raw_github_url(owner: str, repo: str, ref: str, path: str) -> str:
    clean = "/".join(urllib.parse.quote(part) for part in path.strip("/").split("/") if part)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{urllib.parse.quote(ref)}/{clean}"
