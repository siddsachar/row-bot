"""GitHub path source adapter for public skill folders and tap-like listings."""

from __future__ import annotations

import pathlib
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import github_account

from .models import SourceResult, SkillBundle, SkillFile, SkillHubEntry
from .search_index import search_entries
from .sources import (
    SkillSource,
    bundle_from_files,
    classify_file_kind,
    fetch_bytes,
    fetch_json,
    normalize_bundle_path,
    slugify,
    title_from_slug,
)


@dataclass(frozen=True)
class PublicGitHubRoot:
    owner: str
    repo: str
    root: str
    publisher: str
    trust_level: str = "community"
    max_depth: int = 3
    tags: tuple[str, ...] = ()
    ref: str = ""
    enabled_by_default: bool = True

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


PUBLIC_GITHUB_ROOTS = [
    PublicGitHubRoot("NVIDIA", "skills", "skills", "NVIDIA", "trusted_publisher", 3, ("nvidia", "official")),
    PublicGitHubRoot("google", "skills", "skills", "Google", "trusted_publisher", 4, ("google", "google-cloud")),
    PublicGitHubRoot("google-gemini", "gemini-skills", "skills", "Google Gemini", "trusted_publisher", 3, ("gemini", "google")),
    PublicGitHubRoot("microsoft", "skills", ".github/skills", "Microsoft", "trusted_publisher", 3, ("microsoft", "azure")),
    PublicGitHubRoot("MicrosoftDocs", "Agent-Skills", "skills", "MicrosoftDocs", "trusted_publisher", 4, ("azure", "microsoft")),
    PublicGitHubRoot("github", "awesome-copilot", "skills", "GitHub awesome-copilot", "community", 3, ("github-copilot",)),
    PublicGitHubRoot("vercel-labs", "agent-skills", "skills", "Vercel Labs", "community", 3, ("vercel",)),
    PublicGitHubRoot("anthropics", "skills", "skills", "Anthropic", "community", 3, ("anthropic",), "", False),
    PublicGitHubRoot("huggingface", "skills", "skills", "Hugging Face", "community", 3, ("huggingface",), "", False),
    PublicGitHubRoot("garrytan", "gstack", "skills", "garrytan/gstack", "community", 3, ("community",)),
]
LIKELY_SKILL_ROOTS = ["skills", ".claude/skills", ".agents/skills", "agents/skills"]
_GITHUB_BACKOFF_UNTIL = 0
_GITHUB_BACKOFF_MESSAGE = ""


@dataclass(frozen=True)
class GitHubInstallRef:
    owner: str
    repo: str
    path: str = ""
    ref: str = ""
    mode: str = "path"

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    def format(self) -> str:
        ref_part = f"?ref={urllib.parse.quote(self.ref)}" if self.ref else ""
        return f"github:{self.owner}/{self.repo}/{self.path.strip('/')}{ref_part}".rstrip("/")


class GitHubSource(SkillSource):
    id = "github"
    display_name = "GitHub"
    trust_default = "community"
    supports_browse = True
    supports_search = True
    supports_import = True

    def browse(self, limit: int = 50, cursor: str | None = None) -> SourceResult:
        global _GITHUB_BACKOFF_UNTIL, _GITHUB_BACKOFF_MESSAGE
        if _GITHUB_BACKOFF_UNTIL and time.time() < _GITHUB_BACKOFF_UNTIL:
            return SourceResult([], self.id, "error", _GITHUB_BACKOFF_MESSAGE)
        root_results: list[list[SkillHubEntry]] = []
        errors: list[str] = []
        rate_message = ""
        auth_message = self._auth_status_message()
        for root in [item for item in PUBLIC_GITHUB_ROOTS if item.enabled_by_default]:
            try:
                root_results.append(self._list_public_root(root, limit=limit))
            except Exception as exc:
                rate = github_account.rate_limit_from_exception(exc)
                if rate is not None:
                    rate_message = github_account.rate_limit_message(rate)
                    _GITHUB_BACKOFF_MESSAGE = rate_message
                    if rate.reset_epoch:
                        _GITHUB_BACKOFF_UNTIL = max(time.time(), float(rate.reset_epoch))
                    elif rate.retry_after_seconds:
                        _GITHUB_BACKOFF_UNTIL = time.time() + rate.retry_after_seconds
                    break
                errors.append(f"{root.repo_full_name}: {exc}")
        entries = fair_merge_root_entries(root_results, limit=limit)
        message_parts = [part for part in (auth_message, rate_message or "; ".join(errors)) if part]
        message = "; ".join(message_parts)
        if entries and message:
            status = "partial"
        elif entries:
            status = "live"
        elif message:
            status = "error"
        else:
            status = "empty"
        return SourceResult(entries[:limit], self.id, status, message)

    def search(self, query: str, limit: int = 24) -> list[SkillHubEntry]:
        parsed = parse_github_install_ref(query)
        if parsed is None:
            try:
                return search_entries(self.browse(limit=max(limit, 50)).entries, query, limit=limit)
            except Exception:
                return []
        try:
            entries = self._list_skill_entries(parsed, limit=limit)
            if entries:
                return entries[:limit]
        except Exception:
            pass
        if parsed.path:
            name = title_from_slug(pathlib.PurePosixPath(parsed.path).name)
            return [self._entry_from_ref(parsed, name=name, description="GitHub skill path")]
        return []

    def can_resolve(self, value: str) -> bool:
        return parse_github_install_ref(value) is not None

    def resolve(self, value: str) -> SourceResult:
        parsed = parse_github_install_ref(value)
        if parsed is None:
            return SourceResult([], self.id, "empty", "Input is not a GitHub repository or skill path.")
        entries = self.search(value, limit=50)
        if not entries and parsed.path:
            entries = [self._entry_from_ref(parsed, name=title_from_slug(pathlib.PurePosixPath(parsed.path).name))]
        if not entries and not parsed.path:
            for root in LIKELY_SKILL_ROOTS:
                try:
                    entries.extend(self._list_skill_entries(parsed_for_path(parsed, root), limit=50))
                except Exception:
                    continue
        return SourceResult(entries, self.id, "live" if entries else "empty")

    def inspect(self, entry: SkillHubEntry) -> SkillBundle:
        return self.fetch(entry.install_ref)

    def fetch(self, install_ref: str) -> SkillBundle:
        parsed = parse_github_install_ref(install_ref)
        if parsed is None:
            raise ValueError(f"Unsupported GitHub install reference: {install_ref}")
        folder_path = parsed.path
        if pathlib.PurePosixPath(folder_path).name == "SKILL.md":
            folder_path = str(pathlib.PurePosixPath(folder_path).parent)
        files = self._fetch_folder_files(parsed, folder_path)
        root_name = pathlib.PurePosixPath(folder_path).name or parsed.repo
        return bundle_from_files(
            source=self.id,
            install_ref=parsed.format(),
            root_name=root_name,
            files=files,
            metadata={
                "repository": parsed.repo_full_name,
                "path": folder_path,
                "ref": parsed.ref,
                "url": github_web_url(parsed.owner, parsed.repo, folder_path, parsed.ref),
            },
        )

    def _headers(self) -> dict[str, str]:
        return github_account.github_public_api_headers(user_agent="Thoth-Skills-Hub/1.0")

    def _auth_status_message(self) -> str:
        try:
            status = github_account.get_verified_github_account_status(use_cache=True)
        except Exception:
            return ""
        if status.connected:
            return ""
        if status.state in {
            github_account.GITHUB_STATE_INVALID_TOKEN,
            github_account.GITHUB_STATE_RATE_LIMITED,
            github_account.GITHUB_STATE_SECONDARY_LIMITED,
        } and status.anonymous_ok:
            return "GitHub auth needs attention; using anonymous public GitHub access."
        if status.state in {
            github_account.GITHUB_STATE_RATE_LIMITED,
            github_account.GITHUB_STATE_SECONDARY_LIMITED,
        }:
            return status.settings_message or status.message
        return ""

    def _api_url(self, parsed: GitHubInstallRef, path: str) -> str:
        encoded_path = "/".join(urllib.parse.quote(part) for part in path.strip("/").split("/") if part)
        base = f"https://api.github.com/repos/{parsed.owner}/{parsed.repo}/contents"
        url = f"{base}/{encoded_path}" if encoded_path else base
        if parsed.ref:
            url += f"?ref={urllib.parse.quote(parsed.ref)}"
        return url

    def _tree_api_url(self, root: PublicGitHubRoot, ref: str) -> str:
        return (
            f"https://api.github.com/repos/{root.owner}/{root.repo}/git/trees/"
            f"{urllib.parse.quote(ref)}?recursive=1"
        )

    def _repo_tree_api_url(self, parsed: GitHubInstallRef, ref: str) -> str:
        return (
            f"https://api.github.com/repos/{parsed.owner}/{parsed.repo}/git/trees/"
            f"{urllib.parse.quote(ref)}?recursive=1"
        )

    def _list_public_root(self, root: PublicGitHubRoot, *, limit: int) -> list[SkillHubEntry]:
        ref = root.ref or "main"
        try:
            data = fetch_json(self._tree_api_url(root, ref), headers=self._headers())
        except Exception:
            if root.ref:
                raise
            ref = "master"
            data = fetch_json(self._tree_api_url(root, ref), headers=self._headers())
        entries = list_public_root_entries(
            data,
            owner=root.owner,
            repo=root.repo,
            root=root.root,
            ref=ref,
            publisher=root.publisher,
            trust_level=root.trust_level,
            max_depth=root.max_depth,
            tags=list(root.tags),
        )
        return entries[:limit]

    def _list_skill_entries(self, parsed: GitHubInstallRef, *, limit: int) -> list[SkillHubEntry]:
        path = parsed.path or "skills"
        listing = fetch_json(self._api_url(parsed, path), headers=self._headers())
        if isinstance(listing, dict) and listing.get("type") == "file":
            if listing.get("name") == "SKILL.md":
                folder = str(pathlib.PurePosixPath(path).parent)
                return [self._entry_from_ref(parsed_for_path(parsed, folder), name=title_from_slug(pathlib.PurePosixPath(folder).name))]
            return []
        if not isinstance(listing, list):
            return []
        entries: list[SkillHubEntry] = []
        has_skill_here = any(item.get("name") == "SKILL.md" for item in listing if isinstance(item, dict))
        if has_skill_here:
            entries.append(self._entry_from_ref(parsed_for_path(parsed, path), name=title_from_slug(pathlib.PurePosixPath(path).name)))
        for item in listing:
            if len(entries) >= limit:
                break
            if not isinstance(item, dict) or item.get("type") != "dir":
                continue
            child_path = str(item.get("path") or "")
            try:
                child = fetch_json(self._api_url(parsed, child_path), headers=self._headers())
            except Exception:
                continue
            if isinstance(child, list) and any(ch.get("name") == "SKILL.md" for ch in child if isinstance(ch, dict)):
                entries.append(self._entry_from_ref(
                    parsed_for_path(parsed, child_path),
                    name=title_from_slug(item.get("name") or child_path),
                    description=f"GitHub skill in {parsed.repo_full_name}",
                ))
        return entries

    def find_skill_by_name(self, parsed: GitHubInstallRef, skill_name: str) -> SkillHubEntry | None:
        target = slugify(skill_name, fallback="")
        if not target:
            return None
        refs = [parsed.ref] if parsed.ref else ["main", "master"]
        for ref in refs:
            if not ref:
                continue
            try:
                data = fetch_json(self._repo_tree_api_url(parsed, ref), headers=self._headers())
            except Exception:
                continue
            matches = list_matching_skill_entries(
                data,
                owner=parsed.owner,
                repo=parsed.repo,
                ref=ref,
                target=target,
            )
            if matches:
                return matches[0]
        return None

    def _fetch_folder_files(self, parsed: GitHubInstallRef, folder_path: str) -> list[SkillFile]:
        folder_path = folder_path.strip("/")
        files: list[SkillFile] = []

        def visit(path: str) -> None:
            listing = fetch_json(self._api_url(parsed, path), headers=self._headers())
            if isinstance(listing, dict):
                listing_items = [listing]
            elif isinstance(listing, list):
                listing_items = listing
            else:
                raise ValueError(f"Unexpected GitHub API response for {path}")
            for item in listing_items:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "")
                item_path = str(item.get("path") or "")
                rel = relative_to_folder(item_path, folder_path)
                if item_type == "dir":
                    visit(item_path)
                    continue
                if item_type not in {"file", "symlink"}:
                    files.append(SkillFile.from_text(rel or item_path, "", kind="symlink"))
                    continue
                if item_type == "symlink":
                    files.append(SkillFile.from_text(rel or item_path, "", kind="symlink"))
                    continue
                download_url = str(item.get("download_url") or "")
                if not download_url:
                    continue
                content = fetch_bytes(download_url, headers=self._headers())
                rel_path = normalize_bundle_path(rel or pathlib.PurePosixPath(item_path).name)
                files.append(SkillFile.from_bytes(
                    rel_path,
                    content,
                    kind=classify_file_kind(rel_path, content),
                ))

        visit(folder_path)
        return files

    def _entry_from_ref(
        self,
        parsed: GitHubInstallRef,
        *,
        name: str,
        description: str = "GitHub public skill",
    ) -> SkillHubEntry:
        return SkillHubEntry(
            id=f"github:{parsed.repo_full_name}:{parsed.path}:{parsed.ref}",
            name=name,
            description=description,
            source=self.id,
            source_id=parsed.repo_full_name,
            install_ref=parsed.format(),
            url=github_web_url(parsed.owner, parsed.repo, parsed.path, parsed.ref),
            trust_level="community",
            metadata={"repository": parsed.repo_full_name, "path": parsed.path, "ref": parsed.ref},
        )


def parsed_for_path(parsed: GitHubInstallRef, path: str) -> GitHubInstallRef:
    return GitHubInstallRef(parsed.owner, parsed.repo, path.strip("/"), parsed.ref, parsed.mode)


def relative_to_folder(path: str, folder: str) -> str:
    path = normalize_bundle_path(path)
    folder = normalize_bundle_path(folder)
    if folder and path.startswith(folder + "/"):
        return path[len(folder) + 1:]
    if path == folder:
        return pathlib.PurePosixPath(path).name
    return path


def github_web_url(owner: str, repo: str, path: str = "", ref: str = "") -> str:
    branch = urllib.parse.quote(ref or "HEAD")
    clean_path = "/".join(urllib.parse.quote(part) for part in path.strip("/").split("/") if part)
    if clean_path:
        return f"https://github.com/{owner}/{repo}/tree/{branch}/{clean_path}"
    return f"https://github.com/{owner}/{repo}"


def list_public_root_entries(
    data: Any,
    *,
    owner: str,
    repo: str,
    root: str,
    ref: str,
    publisher: str,
    trust_level: str,
    max_depth: int,
    tags: list[str] | tuple[str, ...] = (),
) -> list[SkillHubEntry]:
    raw_items = data.get("tree") if isinstance(data, dict) else data if isinstance(data, list) else []
    if not isinstance(raw_items, list):
        return []
    root_path = normalize_bundle_path(root)
    repo_full = f"{owner}/{repo}"
    entries: list[SkillHubEntry] = []
    seen_folders: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_type = str(raw.get("type") or "")
        mode = str(raw.get("mode") or "")
        path = normalize_bundle_path(str(raw.get("path") or ""))
        if item_type == "symlink" or mode == "120000":
            continue
        if item_type not in {"blob", "file"}:
            continue
        pure = pathlib.PurePosixPath(path)
        if pure.name != "SKILL.md":
            continue
        if root_path and not (path == root_path or path.startswith(root_path + "/")):
            continue
        folder = str(pure.parent)
        if folder == ".":
            folder = ""
        relative_folder = "" if folder == root_path else relative_to_folder(folder, root_path) if root_path else folder
        depth = 0 if not relative_folder else len(pathlib.PurePosixPath(relative_folder).parts)
        if depth > max_depth:
            continue
        if folder in seen_folders:
            continue
        seen_folders.add(folder)
        parsed = GitHubInstallRef(owner, repo, folder, ref)
        name = title_from_slug(pathlib.PurePosixPath(folder).name or repo)
        category = pathlib.PurePosixPath(relative_folder).parts[0] if relative_folder else ""
        root_tags = [str(tag) for tag in tags]
        if category and category not in root_tags:
            root_tags.append(category)
        url = github_web_url(owner, repo, folder, ref)
        entries.append(SkillHubEntry(
            id=f"github:{repo_full}:{folder}:{ref}",
            name=name,
            description=f"GitHub skill from {publisher}",
            source="github",
            source_id=repo_full,
            install_ref=parsed.format(),
            url=url,
            author=publisher,
            tags=root_tags,
            trust_level=trust_level,
            metadata={
                "repository": repo_full,
                "path": folder,
                "ref": ref,
                "root": root_path,
                "publisher": publisher,
                "source_name": "GitHub",
                "category": category,
                "canonical_url": url,
                "trust_level": trust_level,
            },
        ))
    entries.sort(key=lambda entry: (entry.author.lower(), entry.name.lower(), entry.install_ref))
    return entries


def list_matching_skill_entries(
    data: Any,
    *,
    owner: str,
    repo: str,
    ref: str,
    target: str,
) -> list[SkillHubEntry]:
    raw_items = data.get("tree") if isinstance(data, dict) else data if isinstance(data, list) else []
    if not isinstance(raw_items, list):
        return []
    repo_full = f"{owner}/{repo}"
    entries: list[SkillHubEntry] = []
    seen_folders: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_type = str(raw.get("type") or "")
        mode = str(raw.get("mode") or "")
        path = normalize_bundle_path(str(raw.get("path") or ""))
        pure = pathlib.PurePosixPath(path)
        if pure.name != "SKILL.md" or item_type not in {"blob", "file"} or mode == "120000":
            continue
        folder = "" if str(pure.parent) == "." else str(pure.parent)
        folder_name = pathlib.PurePosixPath(folder).name
        normalized_folder = slugify(folder_name, fallback="")
        normalized_path = slugify(path.replace("/", " "), fallback="")
        if normalized_folder != target and f"skills-{target}-skill-md" not in normalized_path and not normalized_path.endswith(f"{target}-skill-md"):
            continue
        if folder in seen_folders:
            continue
        seen_folders.add(folder)
        parsed = GitHubInstallRef(owner, repo, folder, ref)
        url = github_web_url(owner, repo, folder, ref)
        entries.append(SkillHubEntry(
            id=f"github:{repo_full}:{folder}:{ref}",
            name=title_from_slug(folder_name or target),
            description=f"GitHub skill in {repo_full}",
            source="github",
            source_id=repo_full,
            install_ref=parsed.format(),
            url=url,
            trust_level="community",
            metadata={
                "repository": repo_full,
                "path": folder,
                "ref": ref,
                "source_name": "GitHub",
                "canonical_url": url,
                "trust_level": "community",
            },
        ))
    entries.sort(key=lambda entry: (entry.name.lower(), entry.install_ref))
    return entries


def fair_merge_root_entries(root_entries: list[list[SkillHubEntry]], *, limit: int) -> list[SkillHubEntry]:
    merged: list[SkillHubEntry] = []
    seen: set[str] = set()
    index = 0
    while len(merged) < limit:
        added = False
        for entries in root_entries:
            if index >= len(entries):
                continue
            entry = entries[index]
            key = str(entry.metadata.get("canonical_url") or entry.install_ref or entry.id).lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
            added = True
            if len(merged) >= limit:
                break
        if not added:
            break
        index += 1
    return merged


def parse_github_install_ref(value: str) -> GitHubInstallRef | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.startswith("github:"):
        body, _, query = text.removeprefix("github:").partition("?")
        parts = [part for part in body.strip("/").split("/") if part]
        if len(parts) < 2:
            return None
        ref = ""
        if query:
            params = urllib.parse.parse_qs(query)
            ref = (params.get("ref") or [""])[0]
        return GitHubInstallRef(parts[0], parts[1], "/".join(parts[2:]), ref)

    parsed = urllib.parse.urlparse(text)
    if parsed.netloc.lower() == "raw.githubusercontent.com":
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) >= 4:
            return GitHubInstallRef(parts[0], parts[1], "/".join(parts[3:]), parts[2], "raw")
    if parsed.netloc.lower() in {"github.com", "www.github.com"}:
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) < 2:
            return None
        owner, repo = parts[0], parts[1]
        if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
            ref = parts[3]
            path = "/".join(parts[4:])
            return GitHubInstallRef(owner, repo, path, ref, parts[2])
        return GitHubInstallRef(owner, repo, "/".join(parts[2:]))

    shorthand = re.match(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/(.+))?$", text)
    if shorthand:
        return GitHubInstallRef(
            shorthand.group(1),
            shorthand.group(2),
            (shorthand.group(3) or "").strip("/"),
        )
    return None
