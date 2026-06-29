"""Plugin manifest v2 parser and validator.

Parses ``plugin.json`` files and validates the supported Row-Bot plugin
contract.  The v2 contract intentionally limits plugin extension surfaces to
native tools, plugin-packaged MCP servers, channels, and bundled skills.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z][a-z0-9\-]{1,63}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

MANIFEST_SCHEMA_VERSION = 2
SUPPORTED_PROVIDES_KEYS = frozenset({"native_tools", "mcp_servers", "channels", "skills"})
SUPPORTED_PERMISSIONS = frozenset({
    "network",
    "files",
    "account",
    "external_send",
    "messaging",
    "memory_documents",
    "shell_processes",
})
SUPPORTED_SETTING_TYPES = frozenset({
    "text",
    "password",
    "secret",
    "checkbox",
    "select",
    "multi-select",
    "number",
    "url",
    "local_path",
    "textarea",
})
SUPPORTED_AUTH_TYPES = frozenset({
    "api_key",
    "bearer_token",
    "oauth2_pkce",
    "device_code",
    "open_url_paste_code",
})


class ManifestError(Exception):
    """Raised when a plugin.json is invalid."""


@dataclass
class PluginAuthor:
    name: str
    github: str = ""


@dataclass(init=False)
class PluginProvides:
    native_tools: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    channels: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)

    def __init__(
        self,
        *,
        native_tools: list[dict[str, Any]] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        channels: list[dict[str, Any]] | None = None,
        skills: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        # ``tools`` is accepted only for in-process compatibility with existing
        # synthetic PluginManifest construction. JSON manifests must use
        # ``native_tools``.
        if native_tools is None and tools is not None:
            native_tools = tools
        self.native_tools = _dict_list(native_tools)
        self.mcp_servers = _dict_list(mcp_servers)
        self.channels = _dict_list(channels)
        self.skills = _dict_list(skills)

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Compatibility alias for native v2 tools."""
        return self.native_tools

    @tools.setter
    def tools(self, value: list[dict[str, Any]]) -> None:
        self.native_tools = _dict_list(value)


@dataclass
class PluginManifest:
    """Validated representation of a plugin.json file."""

    id: str
    name: str
    version: str
    min_row_bot_version: str
    author: PluginAuthor
    description: str
    long_description: str = ""
    icon: str = "extension"
    license: str = "MIT"
    tags: list[str] = field(default_factory=list)
    homepage: str = ""
    repository: str = ""
    provides: PluginProvides = field(default_factory=PluginProvides)
    schema_version: int = MANIFEST_SCHEMA_VERSION
    permissions: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, Any] = field(default_factory=dict)
    auth: dict[str, Any] = field(default_factory=dict)
    health_checks: list[dict[str, Any]] = field(default_factory=list)
    path: Path | None = None

    @property
    def tool_count(self) -> int:
        return len(self.provides.native_tools)

    @property
    def native_tool_count(self) -> int:
        return len(self.provides.native_tools)

    @property
    def mcp_server_count(self) -> int:
        return len(self.provides.mcp_servers)

    @property
    def channel_count(self) -> int:
        return len(self.provides.channels)

    @property
    def skill_count(self) -> int:
        return len(self.provides.skills)


def parse_manifest(plugin_dir: Path) -> PluginManifest:
    """Parse and validate ``plugin.json`` from *plugin_dir*."""

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


def _validate(raw: dict[str, Any], plugin_dir: Path) -> PluginManifest:
    errors: list[str] = []

    schema_version = raw.get("schema_version")
    if schema_version != MANIFEST_SCHEMA_VERSION:
        errors.append(
            f"'schema_version' must be {MANIFEST_SCHEMA_VERSION}. Got: {schema_version!r}"
        )

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

    author_raw = raw.get("author", {})
    if author_raw in ({}, None):
        author = PluginAuthor(name="Unknown")
    elif not isinstance(author_raw, dict) or not author_raw.get("name"):
        errors.append("'author' must be an object with at least 'name' when provided")
        author = PluginAuthor(name="Unknown")
    else:
        author = PluginAuthor(
            name=str(author_raw.get("name", "")),
            github=str(author_raw.get("github", "")),
        )

    provides_raw = raw.get("provides")
    provides = PluginProvides()
    if not isinstance(provides_raw, dict):
        errors.append("'provides' is required and must be an object")
    else:
        unknown_surfaces = sorted(set(provides_raw) - SUPPORTED_PROVIDES_KEYS)
        if unknown_surfaces:
            errors.append(
                "'provides' contains unsupported extension surface(s): "
                + ", ".join(unknown_surfaces)
            )
        provides = PluginProvides(
            native_tools=_validate_provide_entries(
                provides_raw.get("native_tools", []),
                surface="native_tools",
                required_keys=("id", "entrypoint"),
                errors=errors,
            ),
            mcp_servers=_validate_provide_entries(
                provides_raw.get("mcp_servers", []),
                surface="mcp_servers",
                required_keys=("id",),
                errors=errors,
            ),
            channels=_validate_provide_entries(
                provides_raw.get("channels", []),
                surface="channels",
                required_keys=("id",),
                errors=errors,
            ),
            skills=_validate_provide_entries(
                provides_raw.get("skills", []),
                surface="skills",
                required_keys=("id", "path"),
                errors=errors,
            ),
        )

    permissions = raw.get("permissions", [])
    if permissions is None:
        permissions = []
    if not isinstance(permissions, list):
        errors.append("'permissions' must be a list")
        permissions = []
    permissions = [str(item) for item in permissions if isinstance(item, str)]
    unknown_permissions = sorted(set(permissions) - SUPPORTED_PERMISSIONS)
    if unknown_permissions:
        errors.append(
            "'permissions' contains unsupported value(s): "
            + ", ".join(unknown_permissions)
        )

    settings = raw.get("settings", {})
    if not isinstance(settings, dict):
        errors.append("'settings' must be an object when provided")
        settings = {}
    _validate_declared_fields(settings, "settings", errors)

    secrets = raw.get("secrets", {})
    if not isinstance(secrets, dict):
        errors.append("'secrets' must be an object when provided")
        secrets = {}
    _validate_declared_fields(secrets, "secrets", errors)

    auth = raw.get("auth", {})
    if not isinstance(auth, dict):
        errors.append("'auth' must be an object when provided")
        auth = {}
    _validate_auth(auth, errors)

    health_checks = raw.get("health_checks", [])
    if health_checks is None:
        health_checks = []
    if not isinstance(health_checks, list):
        errors.append("'health_checks' must be a list when provided")
        health_checks = []
    else:
        for index, item in enumerate(health_checks):
            if not isinstance(item, dict):
                errors.append(f"'health_checks[{index}]' must be an object")

    if errors:
        raise ManifestError(
            f"Plugin '{plugin_id or plugin_dir.name}' manifest errors:\n"
            + "\n".join(f"  - {error}" for error in errors)
        )

    return PluginManifest(
        id=plugin_id,
        name=name,
        version=version,
        min_row_bot_version=min_row_bot,
        author=author,
        description=description,
        long_description=str(raw.get("long_description", "")),
        icon=str(raw.get("icon", "extension")),
        license=str(raw.get("license", "MIT")),
        tags=[str(t) for t in raw.get("tags", []) if isinstance(t, str)],
        homepage=str(raw.get("homepage", "")),
        repository=str(raw.get("repository", "")),
        provides=provides,
        schema_version=MANIFEST_SCHEMA_VERSION,
        permissions=permissions,
        settings=settings,
        secrets=secrets,
        auth=auth,
        health_checks=_dict_list(health_checks),
        path=plugin_dir,
    )


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _validate_provide_entries(
    value: Any,
    *,
    surface: str,
    required_keys: tuple[str, ...],
    errors: list[str],
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"'provides.{surface}' must be a list")
        return []
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"'provides.{surface}[{index}]' must be an object")
            continue
        for key in required_keys:
            if not isinstance(item.get(key), str) or not str(item.get(key)).strip():
                errors.append(f"'provides.{surface}[{index}].{key}' is required")
        entries.append(dict(item))
    return entries


def _validate_declared_fields(
    fields: dict[str, Any],
    owner: str,
    errors: list[str],
) -> None:
    for key, spec in fields.items():
        if not isinstance(key, str) or not key:
            errors.append(f"'{owner}' field keys must be non-empty strings")
            continue
        if not isinstance(spec, dict):
            errors.append(f"'{owner}.{key}' must be an object")
            continue
        field_type = str(spec.get("type", "text"))
        if field_type not in SUPPORTED_SETTING_TYPES:
            errors.append(
                f"'{owner}.{key}.type' must be one of "
                f"{', '.join(sorted(SUPPORTED_SETTING_TYPES))}. Got: {field_type!r}"
            )


def _validate_auth(auth: dict[str, Any], errors: list[str]) -> None:
    for key, spec in auth.items():
        if not isinstance(spec, dict):
            errors.append(f"'auth.{key}' must be an object")
            continue
        auth_type = str(spec.get("type", ""))
        if auth_type and auth_type not in SUPPORTED_AUTH_TYPES:
            errors.append(
                f"'auth.{key}.type' must be one of "
                f"{', '.join(sorted(SUPPORTED_AUTH_TYPES))}. Got: {auth_type!r}"
            )
