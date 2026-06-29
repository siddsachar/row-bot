"""Developer workflow helpers for Row-Bot plugins."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.plugins.manifest import PluginManifest, parse_manifest

DATA_DIR = get_row_bot_data_dir()
LINKS_PATH = DATA_DIR / "plugin_links.json"


@dataclass
class PluginValidationResult:
    ok: bool
    plugin_id: str = ""
    path: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)


def validate_plugin_path(path: str | Path) -> PluginValidationResult:
    plugin_dir = Path(path).expanduser().resolve()
    try:
        manifest = parse_manifest(plugin_dir)
    except Exception as exc:
        return PluginValidationResult(False, path=str(plugin_dir), errors=[str(exc)])

    warnings: list[str] = []
    try:
        from row_bot.plugins.loader import _security_scan

        security_error = _security_scan(plugin_dir)
        if security_error:
            return PluginValidationResult(
                False,
                plugin_id=manifest.id,
                path=str(plugin_dir),
                errors=[security_error],
                manifest=_manifest_summary(manifest),
            )
    except Exception as exc:
        warnings.append(f"Security scan skipped: {exc}")

    if manifest.native_tool_count and not (plugin_dir / "plugin_main.py").exists():
        warnings.append("Native tools are declared but plugin_main.py is missing.")

    return PluginValidationResult(
        True,
        plugin_id=manifest.id,
        path=str(plugin_dir),
        warnings=warnings,
        manifest=_manifest_summary(manifest),
    )


def link_plugin(path: str | Path) -> PluginValidationResult:
    result = validate_plugin_path(path)
    if not result.ok:
        return result
    links = _read_links()
    links[result.plugin_id] = result.path
    _write_links(links)
    from row_bot.plugins import state as plugin_state

    plugin_state.mark_plugin_installed(
        result.plugin_id,
        version=str(result.manifest.get("version", "")),
        source="local_link",
        source_ref=result.path,
    )
    return result


def iter_linked_plugin_dirs() -> dict[str, Path]:
    links = _read_links()
    return {
        plugin_id: Path(path).expanduser().resolve()
        for plugin_id, path in links.items()
        if Path(path).expanduser().is_dir()
    }


def find_plugin_dir(plugin_id_or_path: str | Path) -> Path | None:
    candidate = Path(plugin_id_or_path).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    plugin_id = str(plugin_id_or_path)
    links = iter_linked_plugin_dirs()
    if plugin_id in links:
        return links[plugin_id]
    from row_bot.plugins import loader

    installed = loader.PLUGINS_DIR / plugin_id
    if installed.is_dir():
        return installed
    return None


def reload_plugin(plugin_id: str) -> PluginValidationResult:
    plugin_dir = find_plugin_dir(plugin_id)
    if plugin_dir is None:
        return PluginValidationResult(False, plugin_id=plugin_id, errors=[f"Plugin not found: {plugin_id}"])
    from row_bot.channels import registry as channel_registry
    from row_bot.plugins import loader, registry as plugin_registry

    plugin_registry.unregister_plugin(plugin_id)
    channel_registry.unregister_plugin_channels(plugin_id)
    result = loader._load_single_plugin(plugin_dir)
    return PluginValidationResult(
        result.success,
        plugin_id=result.plugin_id,
        path=str(plugin_dir),
        errors=[] if result.success else [result.error],
        warnings=list(result.warnings),
        manifest=_manifest_summary(result.manifest) if result.manifest else {},
    )


def doctor_plugin(plugin_id_or_path: str | Path) -> PluginValidationResult:
    plugin_dir = find_plugin_dir(plugin_id_or_path)
    if plugin_dir is None:
        return PluginValidationResult(False, errors=[f"Plugin not found: {plugin_id_or_path}"])
    result = validate_plugin_path(plugin_dir)
    if not result.ok:
        return result
    manifest = parse_manifest(plugin_dir)
    from row_bot.plugins.ui_settings import _get_missing_secrets, _get_missing_settings

    missing_settings = _get_missing_settings(manifest)
    missing_secrets = _get_missing_secrets(manifest)
    warnings = list(result.warnings)
    if missing_settings:
        warnings.append("Missing required settings: " + ", ".join(missing_settings))
    if missing_secrets:
        warnings.append("Missing required secrets: " + ", ".join(missing_secrets))
    return PluginValidationResult(
        ok=not (missing_settings or missing_secrets),
        plugin_id=manifest.id,
        path=str(plugin_dir),
        errors=[],
        warnings=warnings,
        manifest=_manifest_summary(manifest),
    )


def build_index(root: str | Path, *, source: str = "") -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    index_source = source or str(root_path)
    plugins_dir = root_path / "plugins"
    entries: list[dict[str, Any]] = []
    for plugin_dir in sorted(plugins_dir.iterdir() if plugins_dir.is_dir() else []):
        if not plugin_dir.is_dir():
            continue
        manifest = parse_manifest(plugin_dir)
        rel_path = plugin_dir.relative_to(root_path).as_posix()
        entries.append({
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "author": asdict(manifest.author),
            "tags": list(manifest.tags),
            "path": rel_path,
            "archive_url": "",
            "checksum": _checksum_tree(plugin_dir),
            "provides": {
                "native_tools": manifest.native_tool_count,
                "mcp_servers": manifest.mcp_server_count,
                "channels": manifest.channel_count,
                "skills": manifest.skill_count,
            },
            "permissions": list(manifest.permissions),
            "min_row_bot_version": manifest.min_row_bot_version,
            "changelog_url": "",
        })
    return {
        "schema_version": 2,
        "generated": "",
        "source": index_source,
        "plugins": entries,
    }


def write_index(root: str | Path, *, source: str = "") -> Path:
    root_path = Path(root).expanduser().resolve()
    index = build_index(root_path, source=source)
    path = root_path / "index.json"
    path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return path


def run_cli(args: argparse.Namespace) -> int:
    command = getattr(args, "plugins_command", "")
    if command == "validate":
        return _print_result(validate_plugin_path(args.path))
    if command == "link":
        return _print_result(link_plugin(args.path))
    if command == "reload":
        return _print_result(reload_plugin(args.plugin_id))
    if command == "doctor":
        return _print_result(doctor_plugin(args.plugin))
    raise SystemExit(f"Unknown plugins command: {command}")


def _read_links() -> dict[str, str]:
    if not LINKS_PATH.exists():
        return {}
    try:
        data = json.loads(LINKS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    links = data.get("links", data) if isinstance(data, dict) else {}
    if not isinstance(links, dict):
        return {}
    return {str(key): str(value) for key, value in links.items() if str(key) and str(value)}


def _write_links(links: dict[str, str]) -> None:
    LINKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LINKS_PATH.write_text(json.dumps({"links": links}, indent=2) + "\n", encoding="utf-8")


def _manifest_summary(manifest: PluginManifest | None) -> dict[str, Any]:
    if manifest is None:
        return {}
    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "native_tools": manifest.native_tool_count,
        "mcp_servers": manifest.mcp_server_count,
        "channels": manifest.channel_count,
        "skills": manifest.skill_count,
    }


def _checksum_tree(plugin_dir: Path) -> str:
    return compute_plugin_checksum(plugin_dir)


def compute_plugin_checksum(plugin_dir: str | Path) -> str:
    plugin_path = Path(plugin_dir)
    digest = hashlib.sha256()
    for path in sorted(plugin_path.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(plugin_path).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _print_result(result: PluginValidationResult) -> int:
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.ok else 1
