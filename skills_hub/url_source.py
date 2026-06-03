"""Direct URL source adapter for single-file public SKILL.md imports."""

from __future__ import annotations

import pathlib
import urllib.parse

from .models import SkillBundle, SkillHubEntry
from .sources import (
    SkillSource,
    bundle_from_single_file,
    fetch_text,
    parse_skill_markdown,
    slugify,
    title_from_slug,
)
from .models import SourceResult


class DirectURLSource(SkillSource):
    id = "direct_url"
    display_name = "Direct URL"
    trust_default = "community"
    supports_browse = False
    supports_search = False
    supports_import = True

    def search(self, query: str, limit: int = 24) -> list[SkillHubEntry]:
        return []

    def can_resolve(self, value: str) -> bool:
        return self._looks_like_skill_url(value)

    def resolve(self, value: str) -> SourceResult:
        text = (value or "").strip()
        if not self.can_resolve(text):
            return SourceResult([], self.id, "empty", "Input is not a direct SKILL.md URL.")
        name = self._name_from_url(text)
        return SourceResult([
            SkillHubEntry(
                id=f"direct:{text}",
                name=title_from_slug(name),
                description="Direct SKILL.md import",
                source=self.id,
                source_id=text,
                install_ref=text,
                url=text,
                trust_level="community",
                metadata={"single_file": True, "trust_level": "community", "canonical_url": text},
            )
        ], self.id, "live")

    def inspect(self, entry: SkillHubEntry) -> SkillBundle:
        return self.fetch(entry.install_ref)

    def fetch(self, install_ref: str) -> SkillBundle:
        if not self._looks_like_skill_url(install_ref):
            raise ValueError("Direct URL installs require an HTTP(S) markdown URL")
        text = fetch_text(install_ref)
        frontmatter, _instructions = parse_skill_markdown(text)
        root_name = str(frontmatter.get("name") or self._name_from_url(install_ref))
        return bundle_from_single_file(
            source=self.id,
            install_ref=install_ref,
            root_name=root_name,
            text=text,
            metadata={"url": install_ref, "single_file": True, "trust_level": "community"},
        )

    @staticmethod
    def _looks_like_skill_url(value: str) -> bool:
        parsed = urllib.parse.urlparse(value or "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        suffix = pathlib.PurePosixPath(parsed.path).suffix.lower()
        return parsed.path.endswith("/SKILL.md") or suffix in {".md", ".markdown"}

    @staticmethod
    def _name_from_url(value: str) -> str:
        parsed = urllib.parse.urlparse(value or "")
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            return "imported-skill"
        if parts[-1].lower() == "skill.md" and len(parts) >= 2:
            return slugify(parts[-2], fallback="imported-skill")
        stem = pathlib.PurePosixPath(parts[-1]).stem
        return slugify(stem, fallback="imported-skill")
