"""Plugin manifest parser and validator.

Parses ``plugin.json`` files and validates they conform to the expected
schema.  Returns a ``PluginManifest`` dataclass on success or raises
``ManifestError`` on failure.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Validation constants ─────────────────────────────────────────────────────
_ID_RE = re.compile(r"^[a-z][a-z0-9\-]{1,63}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class ManifestError(Exception):
    """Raised when a plugin.json is invalid."""


# ── Dataclasses ──────────────────────────────────────────────────────────────
@dataclass
class PluginAuthor:
    name: str
    github: str = ""


@dataclass
class PluginProvides:
    tools: list[dict[str, str]] = field(default_factory=list)
    skills: list[dict[str, str]] = field(default_factory=list)


@dataclass
class PluginManifest:
    """Validated, immutable representation of a plugin.json file."""
    id: str
    name: str
    version: str
    min_row_bot_version: str
    author: PluginAuthor
    description: str
    long_description: str = ""
    icon: str = "🔌"
    license: str = "MIT"
    tags: list[str] = field(default_factory=list)
    homepage: str = ""
    repository: str = ""
    provides: PluginProvides = field(default_factory=PluginProvides)
    settings: dict[str, Any] = field(default_factory=dict)
    python_dependencies: list[str] = field(default_factory=list)
    # Path to the plugin directory (set after loading, not in JSON)
    path: Path | None = None

    @property
    def tool_count(self) -> int:
        return len(self.provides.tools)

    @property
    def skill_count(self) -> int:
        return len(self.provides.skills)


# ── Parsing ──────────────────────────────────────────────────────────────────
def parse_manifest(plugin_dir: Path) -> PluginManifest:
    """Parse and validate ``plugin.json`` from *plugin_dir*.

    Raises ``ManifestError`` with a descriptive message on any problem.
    """
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.exists():
        raise ManifestError(f"Missing plugin.json in {plugin_dir}")

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Invalid JSON in {manifest_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ManifestError(f"plugin.json must be a JSON object, got {type(raw).__name__}")

    return _validate(raw, plugin_dir)


def _validate(raw: dict, plugin_dir: Path) -> PluginManifest:
    """Validate raw dict and return a PluginManifest."""
    errors: list[str] = []

    # ── Required string fields ───────────────────────────────────────────
    plugin_id = raw.get("id", "")
    if not isinstance(plugin_id, str) or not _ID_RE.match(plugin_id):
        errors.append(
            f"'id' must be lowercase alphanumeric with hyphens, 2-64 chars. Got: {plugin_id!r}"
        )

    name = raw.get("name", "")
    if not isinstance(name, str) or not name.strip():
        errors.append("'name' is required and must be a non-empty string")

    version = raw.get("version", "")
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        errors.append(f"'version' must be semver (x.y.z). Got: {version!r}")

    min_row_bot = raw.get("min_row_bot_version", "")
    if not isinstance(min_row_bot, str) or not _SEMVER_RE.match(min_row_bot):
        errors.append(f"'min_row_bot_version' must be semver. Got: {min_row_bot!r}")

    description = raw.get("description", "")
    if not isinstance(description, str) or not description.strip():
        errors.append("'description' is required")

    # ── Author ───────────────────────────────────────────────────────────
    author_raw = raw.get("author", {})
    if not isinstance(author_raw, dict) or not author_raw.get("name"):
        errors.append("'author' must be an object with at least 'name'")
        author = PluginAuthor(name="Unknown")
    else:
        author = PluginAuthor(
            name=str(author_raw.get("name", "")),
            github=str(author_raw.get("github", "")),
        )

    # ── Provides ─────────────────────────────────────────────────────────
    provides_raw = raw.get("provides", {})
    provides = PluginProvides()
    if isinstance(provides_raw, dict):
        for tool_entry in provides_raw.get("tools", []):
            if isinstance(tool_entry, dict) and tool_entry.get("name"):
                provides.tools.append(tool_entry)
        for skill_entry in provides_raw.get("skills", []):
            if isinstance(skill_entry, dict) and skill_entry.get("name"):
                provides.skills.append(skill_entry)

    # ── Settings ─────────────────────────────────────────────────────────
    settings = raw.get("settings", {})
    if not isinstance(settings, dict):
        settings = {}

    # ── Python dependencies ──────────────────────────────────────────────
    deps = raw.get("python_dependencies", [])
    if not isinstance(deps, list):
        deps = []

    if errors:
        raise ManifestError(
            f"Plugin '{plugin_id or plugin_dir.name}' manifest errors:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )

    return PluginManifest(
        id=plugin_id,
        name=name,
        version=version,
        min_row_bot_version=min_row_bot,
        author=author,
        description=description,
        long_description=str(raw.get("long_description", "")),
        icon=str(raw.get("icon", "🔌")),
        license=str(raw.get("license", "MIT")),
        tags=[str(t) for t in raw.get("tags", []) if isinstance(t, str)],
        homepage=str(raw.get("homepage", "")),
        repository=str(raw.get("repository", "")),
        provides=provides,
        settings=settings,
        python_dependencies=[str(d) for d in deps],
        path=plugin_dir,
    )
