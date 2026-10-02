"""Agent Plugins 1.0.0: declarative skills and MCP only, with local validation.

No acquisition, registration, schema downloads, or foreign entry point imports.
Upstream bytes are never rewritten; mappings belong to the plugin state owner.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from row_bot.package_files import check_package_tree, contained_path
from row_bot.plugins.manifest import ManifestError, PluginAuthor, PluginManifest, PluginProvides

SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
ADAPTER_VERSION = 1


def package_id(source_identity: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", name).strip("-")[:36] or "package"
    digest = hashlib.sha256((source_identity + "\0" + name).encode()).hexdigest()[:16]
    return f"portable-{slug}-{digest}"


def child_alias(plugin_id: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:30] or "skill"
    digest = hashlib.sha256((plugin_id + "\0" + name).encode()).hexdigest()[:12]
    return f"p_{slug}_{digest}"


def _json(path: Path) -> dict:
    if path.stat().st_size > 512 * 1024:
        raise ValueError("metadata_too_large")
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_json_key")
            value[key] = item
        return value
    raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(raw, dict):
        raise ValueError("metadata_must_be_object")
    return raw


def parse_portable_manifest(root: Path, raw: dict, *, source_identity: str = "") -> PluginManifest:
    """Validate the manifest, then independently discover supported children."""
    try:
        contained_path(root, "plugin.json")
        if raw.get("$schema") != SCHEMA:
            raise ValueError("unsupported_agent_plugins_version")
        name = raw.get("name")
        if (not isinstance(name, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?", name)
                or "--" in name or ".." in name):
            raise ValueError("invalid_portable_name")
        for field in ("version", "description", "homepage", "repository", "license"):
            if field in raw and not isinstance(raw[field], str):
                raise ValueError("invalid_manifest_" + field)
        author = raw.get("author", {})
        if (not isinstance(author, dict) or set(author) - {"name", "email", "url"}
                or any(not isinstance(v, str) for v in author.values())):
            raise ValueError("invalid_manifest_author")
        keywords = raw.get("keywords", [])
        if not isinstance(keywords, list) or any(not isinstance(v, str) for v in keywords):
            raise ValueError("invalid_manifest_keywords")
    except (ValueError, OSError) as exc:
        raise ManifestError(str(exc)) from None
    if not source_identity:
        from row_bot.plugins.state import get_plugin_package_state
        source_identity = str(get_plugin_package_state(root.name).get("source_identity", ""))
    source_identity = source_identity or "local:" + str(root.resolve())
    identity = package_id(source_identity, name)
    known = {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords", "extensions"}
    diagnostics = [{"component": "manifest", "reason": "Ignored unknown field: " + k[:80]} for k in raw if k not in known]
    if "extensions" in raw:
        diagnostics.append({"component": "extensions", "reason": "Foreign client extensions are not executed."})
    skills = _skills(root, identity, diagnostics)
    servers = _servers(root, diagnostics)
    return PluginManifest(id=identity, name=name, version=raw.get("version", ""), min_row_bot_version="0.0.0",
        author=PluginAuthor(author.get("name", "")), description=raw.get("description", ""),
        license=raw.get("license", ""), homepage=raw.get("homepage", ""), repository=raw.get("repository", ""), tags=keywords,
        provides=PluginProvides(skills=skills, mcp_servers=servers), path=root,
        permissions=(["shell_processes"] if any(s["transport"] == "stdio" for s in servers) else [])
                    + (["network"] if any(s["transport"] != "stdio" for s in servers) else []),
        package_format="agent-plugins-1.0.0", source_identity=source_identity, diagnostics=diagnostics)


def _skills(root: Path, plugin_id: str, diagnostics: list[dict]) -> list[dict]:
    from row_bot.skills_hub.models import SkillFile
    from row_bot.skills_hub.sources import bundle_from_files, classify_file_kind
    from row_bot.skills_hub.scanner import scan_bundle

    result = []
    try:
        folder = contained_path(root, "skills")
        if not folder.exists():
            return []
        if not folder.is_dir():
            raise ValueError("skills_must_be_directory")
        children = list(folder.iterdir())
        if len(children) > 128:
            raise ValueError("too_many_skills")
    except (OSError, ValueError) as exc:
        diagnostics.append({"component": "skills", "reason": str(exc)[:160]})
        return []
    for child in sorted(children):
        try:
            contained_path(root, "skills/" + child.name)
            if not child.is_dir() or not (child / "SKILL.md").exists():
                continue
            contained_path(root, "skills/" + child.name + "/SKILL.md")
            check_package_tree(child, max_files=100, max_bytes=5_000_000)
            files = []
            for path in sorted(child.rglob("*")):
                if path.is_file():
                    data = path.read_bytes()
                    rel = path.relative_to(child).as_posix()
                    files.append(SkillFile.from_bytes(rel, data, kind=classify_file_kind(rel, data)))
            bundle = bundle_from_files(source="portable", install_ref="", root_name=child.name, files=files)
            name = bundle.frontmatter.get("name")
            description = bundle.frontmatter.get("description")
            if (not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                    or len(name) > 64 or name != child.name or not isinstance(description, str)
                    or not 1 <= len(description) <= 1024):
                raise ValueError("invalid_agent_skill_metadata")
            scan = scan_bundle(bundle)
            if scan.blocked:
                raise ValueError("skill_scan_blocked")
            result.append({"name": child_alias(plugin_id, name), "upstream_name": name, "display_name": name,
                "description": description, "instructions": bundle.instructions, "root": child,
                "path": "skills/" + child.name, "version": str(bundle.frontmatter.get("version", ""))})
        except (OSError, ValueError) as exc:
            diagnostics.append({"component": "skills/" + child.name, "reason": str(exc)[:160]})
    return result


def _servers(root: Path, diagnostics: list[dict]) -> list[dict]:
    try:
        path = contained_path(root, "mcp.json")
        if not path.exists():
            return []
        raw = _json(path)
        if raw.get("$schema") != MCP_SCHEMA or set(raw) != {"$schema", "mcpServers"} or not isinstance(raw["mcpServers"], dict) or len(raw["mcpServers"]) > 64:
            raise ValueError("invalid_mcp_component")
    except (OSError, ValueError) as exc:
        diagnostics.append({"component": "mcp", "reason": str(exc)[:160]})
        return []
    result = []
    for name, entry in raw["mcpServers"].items():
        try:
            if not name or len(name) > 160 or not isinstance(entry, dict):
                raise ValueError("invalid_server")
            kind = entry.get("type")
            allowed = {"type", "command", "args", "env", "cwd"} if kind == "stdio" else {"type", "url", "headers"}
            if kind not in {"stdio", "streamable-http", "sse"} or set(entry) - allowed:
                raise ValueError("unsupported_server_fields_or_transport")
            if kind == "stdio":
                command = entry.get("command")
                if not isinstance(command, str) or not command or "${" in command:
                    raise ValueError("invalid_executable_token")
                if command.startswith("./"):
                    contained_path(root, command[2:])
                elif re.search(r"[\s/\\:;|&<>]", command) or command in {".", ".."}:
                    raise ValueError("invalid_executable_token")
                args = entry.get("args", [])
                if not isinstance(args, list) or len(args) > 256 or any(not isinstance(a, str) for a in args):
                    raise ValueError("invalid_arguments")
                env = entry.get("env", {})
                if not isinstance(env, dict) or any(not isinstance(v, str) or not k or "=" in k or "\0" in k or k.upper() in {"PLUGIN_ROOT", "PLUGIN_DATA"} for k, v in env.items()):
                    raise ValueError("invalid_environment")
                cwd = entry.get("cwd", "${PLUGIN_ROOT}")
                if not isinstance(cwd, str):
                    raise ValueError("invalid_cwd")
                if cwd.startswith("./"):
                    contained_path(root, cwd[2:])
                elif not any(cwd == v or cwd.startswith(v + "/") for v in ("${PLUGIN_ROOT}", "${PLUGIN_DATA}")):
                    raise ValueError("invalid_cwd")
                else:
                    suffix = cwd.split("}", 1)[1].lstrip("/")
                    if suffix:
                        contained_path(root, suffix)
            else:
                validate_remote(entry.get("url"), entry.get("headers", {}))
            result.append({**entry, "id": name, "transport": "streamable_http" if kind == "streamable-http" else kind, "portable": True})
        except (OSError, ValueError, TypeError) as exc:
            diagnostics.append({"component": "mcp/" + name[:160], "reason": str(exc)[:160]})
    return result


def validate_remote(url: object, headers: object) -> None:
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError("invalid_remote_url")
    parsed = urlsplit(url)
    loopback = parsed.hostname == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        pass
    if (not parsed.hostname or parsed.username or parsed.password or parsed.fragment
            or parsed.scheme not in ({"http", "https"} if loopback else {"https"})):
        raise ValueError("invalid_remote_url")
    if not isinstance(headers, dict) or len({k.lower() for k in headers}) != len(headers):
        raise ValueError("invalid_headers")
    for key, value in headers.items():
        if (not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) or not isinstance(value, str)
                or re.search(r"[\x00-\x1f\x7f]", value)):
            raise ValueError("invalid_headers")


def expand(value: str, *, root: Path, data: Path) -> str:
    """Single non-recursive expansion in the fields specified by the standard."""
    values = {"PLUGIN_ROOT": str(root.resolve()), "PLUGIN_DATA": str(data.resolve())}
    return re.sub(r"\$\{(PLUGIN_ROOT|PLUGIN_DATA)\}", lambda m: values[m[1]], value)
