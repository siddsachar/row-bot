"""Plugin loader — discovery, validation, and lifecycle.

Scans ``~/.row-bot/installed_plugins/`` for plugin directories, validates
their manifests, and loads enabled plugins safely (try/except + timeout).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import logging
import os
import pathlib
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.runtime_paths import app_path
from row_bot.plugins.manifest import PluginManifest, ManifestError, parse_manifest
from row_bot.plugins.api import PluginAPI, PluginTool
from row_bot.plugins import registry as plugin_registry
from row_bot.plugins import state as plugin_state

logger = logging.getLogger(__name__)

DATA_DIR = get_row_bot_data_dir()
PLUGINS_DIR = DATA_DIR / "installed_plugins"
PLUGIN_LOGS_DIR = DATA_DIR / "plugin_logs"

# Timeout for plugin register() calls (seconds)
REGISTER_TIMEOUT = 5.0

# Plugin code may import this public API and ordinary third-party/local modules.
# Row-Bot internals stay behind PluginAPI so plugins cannot bypass lifecycle,
# approval, channel, MCP, or data-access boundaries.
_ALLOWED_CORE_IMPORTS = {"plugins.api", "row_bot.plugins.api"}
_FORBIDDEN_TOP_LEVEL_IMPORTS = {
    "agent",
    "app",
    "channels",
    "documents",
    "dream_cycle",
    "knowledge_graph",
    "memory",
    "memory_extraction",
    "models",
    "prompts",
    "tasks",
    "threads",
    "tools",
    "ui",
}
_FORBIDDEN_UI_FRAMEWORK_IMPORTS = {
    "gradio",
    "nicegui",
    "pywebview",
    "streamlit",
    "webview",
}
_DANGEROUS_BUILTIN_CALLS = {"eval", "exec", "__import__"}


@dataclass
class LoadResult:
    """Summary of a plugin load attempt."""
    plugin_id: str
    success: bool
    manifest: PluginManifest | None = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)


# ── Module-level state ───────────────────────────────────────────────────────
_load_results: list[LoadResult] = []


def _install_plugin_api_compat_aliases() -> None:
    """Preserve the public plugin import path for installed third-party plugins."""
    import row_bot.plugins as _plugins_pkg
    import row_bot.plugins.api as _plugins_api

    sys.modules.setdefault("plugins", _plugins_pkg)
    sys.modules.setdefault("plugins.api", _plugins_api)


# ── Public API ───────────────────────────────────────────────────────────────
def load_plugins() -> list[LoadResult]:
    """Discover and load all installed plugins. Safe to call multiple times.

    Returns a list of LoadResult objects summarising each plugin attempt.
    """
    global _load_results
    _load_results = []

    # Re-read state from disk so enable/disable changes are picked up
    plugin_state.reload()

    if not PLUGINS_DIR.exists():
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Created plugins directory: %s", PLUGINS_DIR)

    try:
        from row_bot.plugins.devtools import iter_linked_plugin_dirs

        linked_dirs = iter_linked_plugin_dirs()
    except Exception:
        linked_dirs = {}

    for entry in sorted(PLUGINS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith((".", "_")):
            continue
        if entry.name in linked_dirs:
            continue

        result = _load_single_plugin(entry)
        _load_results.append(result)

        if result.success:
            logger.info("✅ Plugin '%s' v%s loaded (%d tools, %d skills)",
                         result.plugin_id,
                         result.manifest.version if result.manifest else "?",
                         result.manifest.tool_count if result.manifest else 0,
                         result.manifest.skill_count if result.manifest else 0)
        else:
            logger.warning("❌ Plugin '%s' failed to load: %s",
                            result.plugin_id, result.error)

        for w in result.warnings:
            logger.warning("⚠️  %s", w)

    for plugin_id, linked_dir in sorted(linked_dirs.items()):
        result = _load_single_plugin(linked_dir)
        _load_results.append(result)

        if result.success:
            logger.info(
                "Linked plugin '%s' v%s loaded",
                result.plugin_id,
                result.manifest.version if result.manifest else "?",
            )
        else:
            logger.warning("Linked plugin '%s' failed to load: %s", result.plugin_id, result.error)
        for warning in result.warnings:
            logger.warning("Plugin warning: %s", warning)

    try:
        from row_bot.developer.tool_capsules import (
            list_promoted_capsules,
            register_promoted_capsules_with_plugins,
        )

        capsule_warnings = register_promoted_capsules_with_plugins()
        for capsule in list_promoted_capsules():
            _load_results.append(
                LoadResult(
                    plugin_id=capsule.promoted_plugin_id,
                    success=True,
                    manifest=plugin_registry.get_manifest(capsule.promoted_plugin_id),
                    warnings=[
                        "Custom Tool plugin wrapper; source files remain in the tool folder.",
                    ],
                )
            )
        for warning in capsule_warnings:
            logger.warning("Custom Tool plugin warning: %s", warning)
    except Exception as exc:
        logger.debug("Custom Tool plugin registration skipped: %s", exc)

    loaded = sum(1 for r in _load_results if r.success)
    failed = sum(1 for r in _load_results if not r.success)
    logger.info("Plugin loading complete: %d loaded, %d failed", loaded, failed)

    return _load_results


def get_load_summary() -> dict:
    """Return a summary dict for the status bar / UI."""
    return {
        "total": len(_load_results),
        "loaded": sum(1 for r in _load_results if r.success),
        "failed": sum(1 for r in _load_results if not r.success),
        "results": _load_results,
    }


def get_load_results() -> list[LoadResult]:
    return list(_load_results)


def get_plugin_log_path(plugin_id: str) -> pathlib.Path:
    """Return the persisted JSONL load log path for a plugin."""
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", plugin_id).strip("._")
    return PLUGIN_LOGS_DIR / f"{safe_name or 'unknown'}.jsonl"


def read_plugin_logs(plugin_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Read recent persisted load log entries for the Plugin Center."""
    path = get_plugin_log_path(plugin_id)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _append_plugin_log(result: LoadResult) -> None:
    try:
        PLUGIN_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "plugin_id": result.plugin_id,
            "success": result.success,
            "version": result.manifest.version if result.manifest else "",
            "error": _redact_log_text(result.error),
            "warnings": [_redact_log_text(warning) for warning in result.warnings],
        }
        with open(get_plugin_log_path(result.plugin_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception:
        logger.debug("Plugin load log write skipped for %s", result.plugin_id, exc_info=True)


def _redact_log_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(
        r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*[^,\s;]+",
        r"\1=<redacted>",
        str(text),
    )


# ── Single Plugin Loader ─────────────────────────────────────────────────────
def _load_single_plugin(plugin_dir: pathlib.Path) -> LoadResult:
    result = _load_single_plugin_impl(plugin_dir)
    _append_plugin_log(result)
    return result


def _load_single_plugin_impl(plugin_dir: pathlib.Path) -> LoadResult:
    """Load a single plugin from its directory. Never raises."""
    plugin_id = plugin_dir.name

    # Step 1: Parse manifest
    try:
        manifest = parse_manifest(plugin_dir)
        plugin_id = manifest.id
    except ManifestError as exc:
        return LoadResult(plugin_id=plugin_id, success=False, error=str(exc))
    except Exception as exc:
        return LoadResult(plugin_id=plugin_id, success=False,
                          error=f"Unexpected error parsing manifest: {exc}")

        # Step 2: Check Row-Bot version compatibility
    compat_err = _check_version_compat(manifest)
    if compat_err:
        return LoadResult(plugin_id=plugin_id, success=False,
                          manifest=manifest, error=compat_err)

    # Step 3: Check if enabled
    if not plugin_state.is_plugin_enabled(plugin_id):
        # Still register the manifest so the plugin appears in the UI
        # (users need to see the card to re-enable the plugin).
        try:
            from row_bot.channels import registry as channel_registry

            channel_registry.unregister_plugin_channels(plugin_id)
        except Exception:
            logger.debug("Plugin channel unregister skipped for %s", plugin_id, exc_info=True)
        plugin_registry.register_plugin(
            manifest=manifest, tools=[], skills=[],
        )
        return LoadResult(
            plugin_id=plugin_id, success=True, manifest=manifest,
            warnings=["Plugin is disabled — skipping tool/skill registration"]
        )

    # Step 4: Security scan
    sec_err = _security_scan(plugin_dir)
    if sec_err:
        return LoadResult(plugin_id=plugin_id, success=False,
                          manifest=manifest, error=sec_err)

    # Step 5: Import and call register()
    try:
        api = PluginAPI(
            plugin_id=plugin_id,
            plugin_dir=plugin_dir,
            state_backend=plugin_state,
        )
        _call_register_with_timeout(plugin_dir, api)
    except TimeoutError:
        return LoadResult(
            plugin_id=plugin_id, success=False, manifest=manifest,
            error=f"Plugin register() timed out after {REGISTER_TIMEOUT}s"
        )
    except Exception as exc:
        return LoadResult(
            plugin_id=plugin_id, success=False, manifest=manifest,
            error=f"Plugin register() crashed: {exc}"
        )

    # Step 6: Discover skills from skills/ directory
    # Step 7: Register with plugin registry
    try:
        skills = _discover_plugin_skills(plugin_dir)
        for skill in skills:
            api.register_skill(skill)

        warnings = plugin_registry.register_plugin(
            manifest=manifest,
            tools=api._registered_tools,
            skills=api._registered_skills,
        )
        _register_plugin_channels(manifest, api._registered_channels)
    except Exception as exc:
        return LoadResult(
            plugin_id=plugin_id, success=False, manifest=manifest,
            error=f"Skill discovery / registry failed: {exc}"
        )

    return LoadResult(
        plugin_id=plugin_id, success=True, manifest=manifest,
        warnings=warnings,
    )


def _register_plugin_channels(manifest: PluginManifest, channels: list[Any]) -> None:
    if not channels:
        return
    from row_bot.channels import registry as channel_registry

    channel_registry.unregister_plugin_channels(manifest.id)
    source = channel_registry.ChannelSource(
        kind="plugin",
        plugin_id=manifest.id,
        label=manifest.name,
    )
    for channel in channels:
        channel_registry.register(channel, source=source)


# ── Version Check ────────────────────────────────────────────────────────────
def _check_version_compat(manifest: PluginManifest) -> str | None:
    """Check min_row_bot_version. Returns error string or None."""
    try:
        from importlib.metadata import version as pkg_version
        # Try to get Row-Bot's version from various sources
        row_bot_version = _get_row_bot_version()
        if row_bot_version and manifest.min_row_bot_version:
            if _version_tuple(row_bot_version) < _version_tuple(manifest.min_row_bot_version):
                return (
                    f"Requires Row-Bot >= {manifest.min_row_bot_version}, "
                    f"but current version is {row_bot_version}"
                )
    except Exception:
        pass  # Skip version check if we can't determine Row-Bot version
    return None


def _get_row_bot_version() -> str | None:
    """Try to get Row-Bot's current version."""
    # Check if there's a VERSION file or version in app.py
    version_file = app_path("VERSION")
    if version_file.exists():
        return version_file.read_text().strip()
    # Try to extract from RELEASE_NOTES.md
    rn = app_path("RELEASE_NOTES.md")
    if rn.exists():
        try:
            with open(rn, "r", encoding="utf-8") as f:
                for line in f:
                    m = re.search(r"##\s+v(\d+\.\d+\.\d+)", line)
                    if m:
                        return m.group(1)
        except Exception:
            pass
    return None


def _version_tuple(v: str) -> tuple[int, ...]:
    """Convert '3.12.0' to (3, 12, 0)."""
    return tuple(int(x) for x in v.split(".") if x.isdigit())


# ── Security Scan ────────────────────────────────────────────────────────────
def _security_scan(plugin_dir: pathlib.Path) -> str | None:
    """Scan plugin Python files for blocked calls and core imports.

    Returns error string or None.
    """
    for py_file in plugin_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError as exc:
            rel_path = py_file.relative_to(plugin_dir)
            return f"Security violation in {rel_path}:{exc.lineno}: Python syntax error: {exc.msg}"

        rel_path = py_file.relative_to(plugin_dir)
        error = _scan_ast_for_security_errors(tree, rel_path)
        if error:
            return error

    return None


def _scan_ast_for_security_errors(tree: ast.AST, rel_path: pathlib.Path) -> str | None:
    os_aliases = {"os"}
    os_system_aliases: set[str] = set()
    subprocess_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                forbidden = _forbidden_import(module)
                if forbidden:
                    return _security_error(rel_path, node.lineno, forbidden)
                if module == "os":
                    os_aliases.add(alias.asname or "os")
                if module == "subprocess" or module.startswith("subprocess."):
                    subprocess_aliases.add(alias.asname or module.split(".", 1)[0])
                    return _dangerous_error(rel_path, node.lineno, "subprocess")

        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            module = node.module or ""
            forbidden = _forbidden_import(module)
            if forbidden:
                return _security_error(rel_path, node.lineno, forbidden)
            if module == "subprocess" or module.startswith("subprocess."):
                return _dangerous_error(rel_path, node.lineno, "subprocess")
            if module == "os":
                for alias in node.names:
                    if alias.name == "system":
                        os_system_aliases.add(alias.asname or alias.name)

        elif isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                if target.id in _DANGEROUS_BUILTIN_CALLS:
                    return _dangerous_error(rel_path, node.lineno, target.id)
                if target.id in os_system_aliases:
                    return _dangerous_error(rel_path, node.lineno, "os.system")
                if target.id in subprocess_aliases:
                    return _dangerous_error(rel_path, node.lineno, "subprocess")
            elif isinstance(target, ast.Attribute):
                if (
                    target.attr == "system"
                    and isinstance(target.value, ast.Name)
                    and target.value.id in os_aliases
                ):
                    return _dangerous_error(rel_path, node.lineno, "os.system")
                if isinstance(target.value, ast.Name) and target.value.id in subprocess_aliases:
                    return _dangerous_error(rel_path, node.lineno, "subprocess")

    return None


def _forbidden_import(module: str) -> str | None:
    if not module:
        return None
    if module in _ALLOWED_CORE_IMPORTS:
        return None
    if module == "subprocess" or module.startswith("subprocess."):
        return "subprocess"
    if module == "row_bot" or module.startswith("row_bot."):
        return module
    if module == "plugins" or module.startswith("plugins."):
        return module
    top_level = module.split(".", 1)[0]
    if top_level in _FORBIDDEN_TOP_LEVEL_IMPORTS | _FORBIDDEN_UI_FRAMEWORK_IMPORTS:
        return module
    return None


def _dangerous_error(rel_path: pathlib.Path, line_no: int, name: str) -> str:
    return (
        f"Security violation in {rel_path}:{line_no}: forbidden pattern "
        f"'{name}' detected. Plugins must not use eval(), exec(), os.system(), "
        f"subprocess, or __import__()."
    )


def _security_error(rel_path: pathlib.Path, line_no: int, module: str) -> str:
    return (
        f"Security violation in {rel_path}:{line_no}: plugins must not import "
        f"module '{module}'. Use plugins.api and the native Plugin Center contract instead."
    )


# ── Plugin Registration ──────────────────────────────────────────────────────
def _call_register_with_timeout(plugin_dir: pathlib.Path, api: PluginAPI) -> None:
    """Import plugin_main.py and call register(api) with timeout."""
    main_path = plugin_dir / "plugin_main.py"
    if not main_path.exists():
        raise FileNotFoundError(f"Missing plugin_main.py in {plugin_dir}")

    # Add plugin dir to sys.path temporarily for local imports
    plugin_dir_str = str(plugin_dir)
    if plugin_dir_str not in sys.path:
        sys.path.insert(0, plugin_dir_str)
    _install_plugin_api_compat_aliases()

    error_holder: list[Exception] = []

    def _do_register():
        try:
            spec = importlib.util.spec_from_file_location(
                f"_row_bot_plugin_{api.plugin_id}", main_path
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot create module spec for {main_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            register_fn = getattr(module, "register", None)
            if register_fn is None:
                raise AttributeError("plugin_main.py has no register() function")

            register_fn(api)
        except Exception as exc:
            error_holder.append(exc)

    thread = threading.Thread(target=_do_register, daemon=True)
    thread.start()
    thread.join(timeout=REGISTER_TIMEOUT)

    # Restore sys.path
    if plugin_dir_str in sys.path:
        sys.path.remove(plugin_dir_str)

    if thread.is_alive():
        raise TimeoutError(f"register() timed out after {REGISTER_TIMEOUT}s")

    if error_holder:
        raise error_holder[0]


# ── Skill Discovery ─────────────────────────────────────────────────────────
def _discover_plugin_skills(plugin_dir: pathlib.Path) -> list[dict]:
    """Discover SKILL.md files in plugin's skills/ directory."""
    skills_dir = plugin_dir / "skills"
    if not skills_dir.is_dir():
        return []

    import yaml
    skills: list[dict] = []

    for skill_dir in sorted(skills_dir.iterdir()):
        skill_md = None
        if skill_dir.is_dir():
            skill_md = skill_dir / "SKILL.md"
        elif skill_dir.is_file() and skill_dir.name == "SKILL.md":
            skill_md = skill_dir

        if skill_md is None or not skill_md.exists():
            continue

        try:
            content = skill_md.read_text(encoding="utf-8")
            # Parse YAML frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                    instructions = parts[2].strip()
                else:
                    frontmatter = {}
                    instructions = content
            else:
                frontmatter = {}
                instructions = content

            skill_name = frontmatter.get("name", skill_dir.stem if skill_dir.is_dir() else "unnamed")
            skills.append({
                "name": skill_name,
                "display_name": frontmatter.get("display_name", skill_name),
                "icon": frontmatter.get("icon", "🔌"),
                "description": frontmatter.get("description", ""),
                "instructions": instructions,
            })
        except Exception as exc:
            logger.warning("Failed to parse skill %s: %s", skill_md, exc)

    return skills


# ── Reset (for testing) ─────────────────────────────────────────────────────
def _reset():
    """Reset load state. For testing only."""
    global _load_results
    _load_results = []
