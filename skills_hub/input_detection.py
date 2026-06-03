"""Unified detection for public skill source inputs."""

from __future__ import annotations

import re
import urllib.parse

import yaml

from .models import DetectedSourceInput
from .sources import FRONTMATTER_RE

_GITHUB_SHORTHAND_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/(.+))?$")
_KNOWN_MARKETPLACE_HOSTS = {
    "skills.sh": "skills_sh",
    "www.skills.sh": "skills_sh",
    "browse.sh": "browse_sh",
    "www.browse.sh": "browse_sh",
    "clawhub.ai": "clawhub",
    "www.clawhub.ai": "clawhub",
    "chat-agents.lobehub.com": "lobehub",
    "lobehub.com": "lobehub",
    "www.lobehub.com": "lobehub",
}


def detect_source_input(value: str) -> DetectedSourceInput:
    text = str(value or "").strip()
    if not text:
        return DetectedSourceInput(kind="empty", value="", confidence=1.0)

    markdown = _detect_pasted_markdown(text)
    if markdown is not None:
        return markdown

    parsed = urllib.parse.urlparse(text)
    host = parsed.netloc.lower()
    path = parsed.path or ""

    if host in {"github.com", "www.github.com", "raw.githubusercontent.com"}:
        return DetectedSourceInput(
            kind="github_url",
            value=text,
            normalized=text,
            source_id="github",
            metadata={"host": host},
        )

    shorthand = _GITHUB_SHORTHAND_RE.match(text)
    if shorthand and not parsed.scheme:
        return DetectedSourceInput(
            kind="github_shorthand",
            value=text,
            normalized=text,
            source_id="github",
            metadata={
                "owner": shorthand.group(1),
                "repo": shorthand.group(2),
                "path": shorthand.group(3) or "",
            },
        )

    if parsed.scheme in {"http", "https"} and host:
        if path.endswith("/.well-known/skills/index.json"):
            return DetectedSourceInput(
                kind="well_known_index_url",
                value=text,
                normalized=text,
                source_id="well_known",
                metadata={"host": host},
            )

        if _looks_like_skill_markdown_url(path):
            return DetectedSourceInput(
                kind="direct_skill_url",
                value=text,
                normalized=text,
                source_id="direct_url",
                metadata={"host": host},
            )

        if host in _KNOWN_MARKETPLACE_HOSTS:
            return DetectedSourceInput(
                kind="marketplace_url",
                value=text,
                normalized=text,
                source_id=_KNOWN_MARKETPLACE_HOSTS[host],
                metadata={"host": host},
            )

        if _looks_like_website_url(parsed):
            index_url = urllib.parse.urlunparse(
                parsed._replace(path="/.well-known/skills/index.json", params="", query="", fragment="")
            )
            return DetectedSourceInput(
                kind="website_url",
                value=text,
                normalized=index_url,
                source_id="well_known",
                metadata={"host": host, "index_url": index_url},
            )

        return DetectedSourceInput(
            kind="unknown_url",
            value=text,
            normalized=text,
            confidence=0.4,
            metadata={"host": host},
        )

    return DetectedSourceInput(kind="keyword", value=text, normalized=text, confidence=0.7)


def _detect_pasted_markdown(text: str) -> DetectedSourceInput | None:
    if len(text) < 20 or "\n" not in text:
        return None
    frontmatter = {}
    requires_name = False
    match = FRONTMATTER_RE.match(text)
    if match:
        try:
            parsed = yaml.safe_load(match.group(1))
            frontmatter = parsed if isinstance(parsed, dict) else {}
        except Exception:
            frontmatter = {}
        requires_name = not bool(str(frontmatter.get("name") or "").strip())
        return DetectedSourceInput(
            kind="pasted_markdown",
            value=text,
            normalized="pasted_markdown",
            source_id="pasted_markdown",
            metadata={"frontmatter": frontmatter, "requires_name": requires_name},
        )

    heading = _first_markdown_heading(text)
    skillish = bool(heading) and any(
        marker in text.lower()
        for marker in ("when to use", "instructions", "workflow", "steps", "skill")
    )
    if skillish:
        return DetectedSourceInput(
            kind="pasted_markdown",
            value=text,
            normalized="pasted_markdown",
            source_id="pasted_markdown",
            metadata={"heading": heading, "requires_name": not bool(heading)},
        )
    return None


def _first_markdown_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return ""


def _looks_like_skill_markdown_url(path: str) -> bool:
    lower = path.lower()
    return lower.endswith("/skill.md") or lower.endswith(".md") or lower.endswith(".markdown")


def _looks_like_website_url(parsed: urllib.parse.ParseResult) -> bool:
    suffix = parsed.path.rsplit("/", 1)[-1].lower()
    return not suffix or "." not in suffix
