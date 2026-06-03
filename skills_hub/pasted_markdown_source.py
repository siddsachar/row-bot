"""Import adapter for pasted full SKILL.md markdown."""

from __future__ import annotations

import hashlib

from .input_detection import detect_source_input
from .models import SourceResult, SkillBundle, SkillHubEntry
from .sources import (
    SkillSource,
    bundle_from_single_file,
    markdown_with_frontmatter,
    normalize_skill_name,
    parse_skill_markdown,
    title_from_slug,
)


class PastedMarkdownSource(SkillSource):
    id = "pasted_markdown"
    display_name = "Pasted Markdown"
    trust_default = "community"
    supports_browse = False
    supports_search = False
    supports_import = True

    def can_resolve(self, value: str) -> bool:
        return detect_source_input(value).kind == "pasted_markdown"

    def resolve(self, value: str) -> SourceResult:
        if not self.can_resolve(value):
            return SourceResult([], self.id, "empty", "Input is not pasted skill markdown.")
        text = value.strip()
        frontmatter = {}
        name = ""
        try:
            frontmatter, _body = parse_skill_markdown(text)
            name = str(frontmatter.get("name") or "").strip()
        except Exception:
            heading = _first_heading(text)
            name = heading or "Imported Skill"
            text = markdown_with_frontmatter(text, name=name)
            frontmatter, _body = parse_skill_markdown(text)
        if not name:
            name = str(frontmatter.get("display_name") or "Imported Skill")
            text = markdown_with_frontmatter(text, name=name)
            frontmatter, _body = parse_skill_markdown(text)
        slug = normalize_skill_name(name)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        entry = SkillHubEntry(
            id=f"pasted:{digest[:16]}",
            name=str(frontmatter.get("display_name") or title_from_slug(slug)),
            description=str(frontmatter.get("description") or "Pasted public skill markdown"),
            source=self.id,
            source_id="pasted",
            install_ref=f"pasted:{digest}",
            url="",
            author=str(frontmatter.get("author") or "Pasted Markdown"),
            tags=[str(tag) for tag in frontmatter.get("tags", [])] if isinstance(frontmatter.get("tags"), list) else [],
            trust_level="community",
            metadata={
                "markdown": text,
                "content_hash": digest,
                "trust_level": "community",
                "requires_name": False,
            },
        )
        return SourceResult([entry], self.id, "live")

    def inspect(self, entry: SkillHubEntry) -> SkillBundle:
        text = str(entry.metadata.get("markdown") or "")
        if not text:
            raise ValueError("Pasted markdown preview is no longer available.")
        return self._bundle(entry.install_ref, text)

    def fetch(self, install_ref: str) -> SkillBundle:
        raise ValueError("Pasted markdown sources can only be installed from the current preview.")

    def _bundle(self, install_ref: str, text: str) -> SkillBundle:
        frontmatter, _body = parse_skill_markdown(text)
        root_name = normalize_skill_name(str(frontmatter.get("name") or frontmatter.get("display_name") or "imported_skill"))
        return bundle_from_single_file(
            source=self.id,
            install_ref=install_ref,
            root_name=root_name,
            text=text,
            metadata={"trust_level": "community", "pasted": True},
        )


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""
