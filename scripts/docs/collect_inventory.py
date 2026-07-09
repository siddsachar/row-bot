"""Collect public-docs inventory from Row-Bot source files.

The inventory intentionally avoids importing the NiceGUI app. It scans stable
source locations and emits deterministic JSON that generated docs can consume.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.docs.schemas import (
    DocsPageRecord,
    public_route_for_doc,
    repo_path,
    slugify,
    to_jsonable,
    write_json,
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(_read_text(path)) or {}
    return data if isinstance(data, dict) else {}


def _frontmatter(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    data: dict[str, Any] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _parse_ast(path: Path) -> ast.Module | None:
    try:
        return ast.parse(_read_text(path), filename=str(path))
    except SyntaxError:
        return None


def _first_docstring_summary(path: Path) -> str:
    tree = _parse_ast(path)
    if tree is None:
        return ""
    doc = ast.get_docstring(tree) or ""
    summary = " ".join(doc.strip().split())
    return summary.split(". ")[0].strip()


def _literal_value(node: ast.AST) -> Any:
    if isinstance(node, ast.Dict):
        data: dict[Any, Any] = {}
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                continue
            key = _literal_value(key_node)
            data[key] = _literal_value(value_node)
        return data
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [_literal_value(item) for item in node.elts]
    try:
        return ast.literal_eval(node)
    except Exception:
        if isinstance(node, ast.Call):
            return _literal_call(node)
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Name):
            return node.id
    return None


def _literal_call(node: ast.Call) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg:
            data[kw.arg] = _literal_value(kw.value)
    return data


def _assigned_dict(path: Path, name: str) -> dict[str, Any]:
    tree = _parse_ast(path)
    if tree is None:
        return {}
    for node in tree.body:
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            value_node = node.value
        if value_node is not None:
            value = _literal_value(value_node)
            return value if isinstance(value, dict) else {}
    return {}


def _class_names(path: Path) -> list[str]:
    tree = _parse_ast(path)
    if tree is None:
        return []
    return sorted(node.name for node in tree.body if isinstance(node, ast.ClassDef))


def _skill_frontmatter(path: Path) -> dict[str, Any]:
    data = _frontmatter(path)
    text = _read_text(path)
    if "description" not in data:
        for line in text.splitlines():
            if line.lower().startswith("description:"):
                data["description"] = line.split(":", 1)[1].strip()
                break
    if "name" not in data:
        match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        if match:
            data["name"] = match.group(1).strip()
    return data


def collect_tools() -> list[dict[str, Any]]:
    tools_dir = ROOT / "src" / "row_bot" / "tools"
    guide_by_id = {
        path.parent.name.removesuffix("_guide"): path
        for path in (ROOT / "tool_guides").glob("*/SKILL.md")
    }
    tools: list[dict[str, Any]] = []
    for path in sorted(tools_dir.glob("*_tool.py")):
        tool_id = path.stem.removesuffix("_tool")
        guide = guide_by_id.get(tool_id)
        guide_meta = _skill_frontmatter(guide) if guide else {}
        tools.append(
            {
                "id": tool_id,
                "title": guide_meta.get("name") or tool_id.replace("_", " ").title(),
                "description": guide_meta.get("description") or _first_docstring_summary(path),
                "source": repo_path(ROOT, path),
                "guide": repo_path(ROOT, guide) if guide else "",
                "classes": _class_names(path),
            }
        )
    return tools


def collect_providers() -> list[dict[str, Any]]:
    catalog = _assigned_dict(ROOT / "src" / "row_bot" / "providers" / "catalog.py", "PROVIDER_DEFINITIONS")
    providers: list[dict[str, Any]] = []
    for provider_id, raw in sorted(catalog.items()):
        data = raw if isinstance(raw, dict) else {}
        auth_methods = data.get("auth_methods") or []
        if isinstance(auth_methods, tuple):
            auth_methods = list(auth_methods)
        providers.append(
            {
                "id": provider_id,
                "title": data.get("display_name") or provider_id.replace("_", " ").title(),
                "description": _provider_description(provider_id, data),
                "source": "src/row_bot/providers/catalog.py",
                "auth_methods": [str(item) for item in auth_methods],
                "transport": str(data.get("default_transport") or ""),
                "base_url": str(data.get("base_url") or ""),
                "risk_label": str(data.get("risk_label") or "api_key"),
                "experimental": bool(data.get("experimental")),
            }
        )
    return providers


def _provider_description(provider_id: str, data: dict[str, Any]) -> str:
    risk = str(data.get("risk_label") or "")
    if provider_id == "ollama":
        return "Local Ollama models running on this machine."
    if risk == "subscription":
        return "Subscription-backed provider path using local sign-in or an external CLI."
    if risk == "third_party_router":
        return "Third-party model router provider configured with an API key."
    if data.get("base_url"):
        return "Hosted model provider configured with an API key."
    return "Model provider supported by Row-Bot."


def collect_settings() -> list[dict[str, Any]]:
    settings = _load_yaml(ROOT / "docs-content" / "metadata" / "settings.yml").get("tabs", {})
    rows: list[dict[str, Any]] = []
    if isinstance(settings, dict):
        for name, meta in settings.items():
            meta = meta if isinstance(meta, dict) else {}
            rows.append(
                {
                    "id": slugify(name),
                    "title": name,
                    "description": str(meta.get("description") or ""),
                    "docs_route": str(meta.get("docs_route") or ""),
                    "screenshot_id": str(meta.get("screenshot_id") or ""),
                    "source": "docs-content/metadata/settings.yml",
                }
            )
    return rows


def collect_home_tabs() -> list[dict[str, Any]]:
    tabs = _load_yaml(ROOT / "docs-content" / "metadata" / "home_tabs.yml").get("tabs", {})
    rows: list[dict[str, Any]] = []
    if isinstance(tabs, dict):
        for name, meta in tabs.items():
            meta = meta if isinstance(meta, dict) else {}
            rows.append(
                {
                    "id": slugify(name),
                    "title": name,
                    "docs_route": str(meta.get("docs_route") or ""),
                    "screenshot_id": str(meta.get("screenshot_id") or ""),
                    "source": str(meta.get("source") or "src/row_bot/ui/home.py"),
                    "builder": str(meta.get("builder") or "build_home"),
                }
            )
    return rows


def collect_channels() -> list[dict[str, Any]]:
    channels_dir = ROOT / "src" / "row_bot" / "channels"
    skip = {
        "__init__",
        "auth",
        "auth_store",
        "agent_output",
        "approval",
        "base",
        "commands",
        "config",
        "media",
        "media_capture",
        "registry",
        "runtime",
        "streaming",
        "thread_notifications",
        "thread_repair",
        "tool_factory",
    }
    rows: list[dict[str, Any]] = []
    for path in sorted(channels_dir.glob("*.py")):
        if path.stem in skip or path.stem.startswith("_"):
            continue
        text = _read_text(path)
        if "Channel" not in text:
            continue
        display = _regex_return_for_property(text, "display_name") or path.stem.replace("_", " ").title()
        name = _regex_return_for_property(text, "name") or path.stem
        rows.append(
            {
                "id": slugify(name),
                "title": display,
                "description": _first_docstring_summary(path) or f"{display} messaging channel.",
                "source": repo_path(ROOT, path),
                "configured_by": _channel_config_fields(text),
                "capabilities": _channel_capabilities(text),
            }
        )
    return rows


def _regex_return_for_property(text: str, prop: str) -> str:
    pattern = rf"def\s+{re.escape(prop)}\(self\).*?return\s+['\"]([^'\"]+)['\"]"
    match = re.search(pattern, text, flags=re.DOTALL)
    return match.group(1) if match else ""


def _channel_config_fields(text: str) -> list[str]:
    labels = re.findall(r"ConfigField\([^)]*label\s*=\s*['\"]([^'\"]+)['\"]", text, flags=re.DOTALL)
    return sorted(set(labels))


def _channel_capabilities(text: str) -> list[str]:
    match = re.search(r"ChannelCapabilities\((.*?)\)", text, flags=re.DOTALL)
    if not match:
        return []
    return sorted(re.findall(r"([a-z_]+)\s*=\s*True", match.group(1)))


def collect_skills(root_name: str) -> list[dict[str, Any]]:
    skills_root = ROOT / root_name
    skills: list[dict[str, Any]] = []
    if not skills_root.exists():
        return skills
    for path in sorted(skills_root.glob("*/SKILL.md")):
        meta = _skill_frontmatter(path)
        skills.append(
            {
                "id": path.parent.name,
                "title": meta.get("display_name") or meta.get("name") or path.parent.name.replace("_", " ").title(),
                "description": meta.get("description", ""),
                "kind": root_name,
                "source": repo_path(ROOT, path),
            }
        )
    return skills


def collect_mcp() -> list[dict[str, Any]]:
    path = ROOT / "src" / "row_bot" / "mcp_client" / "recommended_servers.json"
    try:
        raw = json.loads(_read_text(path))
    except Exception:
        raw = []
    rows: list[dict[str, Any]] = []
    for entry in raw if isinstance(raw, list) else raw.get("servers", []):
        if not isinstance(entry, dict):
            continue
        install = entry.get("install") if isinstance(entry.get("install"), dict) else {}
        rows.append(
            {
                "id": str(entry.get("id") or slugify(entry.get("name") or "mcp")),
                "title": str(entry.get("name") or entry.get("id") or "MCP server"),
                "description": str(entry.get("description") or ""),
                "source": repo_path(ROOT, path),
                "category": str(entry.get("category") or ""),
                "transport": str(install.get("transport") or ""),
                "command": str(install.get("command") or ""),
                "overlaps_native": entry.get("overlaps_native") or [],
            }
        )
    return rows


def collect_plugins() -> list[dict[str, Any]]:
    manifest = ROOT / "src" / "row_bot" / "plugins" / "manifest.py"
    text = _read_text(manifest)
    fields = re.findall(r"^\s{4}([a-zA-Z_][a-zA-Z0-9_]*)\s*:", text, flags=re.MULTILINE)
    return [
        {
            "id": "plugin-manifest",
            "title": "Plugin manifest",
            "description": "Schema and validation behavior for plugin.json files.",
            "source": repo_path(ROOT, manifest),
            "required_fields": [
                "id",
                "name",
                "version",
                "min_row_bot_version",
                "author",
                "description",
            ],
            "fields": fields,
            "id_pattern": "lowercase letters, numbers, and hyphens; 2-64 characters",
            "version_pattern": "semver x.y.z",
        },
        {
            "id": "custom-tools",
            "title": "Custom Tools",
            "description": "Reviewed Developer Studio tools can be promoted into the plugin-style tool surface.",
            "source": "src/row_bot/plugins/ui_settings.py",
            "required_fields": [],
            "fields": ["name", "description", "tools", "source_url", "installed_path"],
        },
    ]


def collect_data_paths() -> list[dict[str, Any]]:
    path = ROOT / "src" / "row_bot" / "data_paths.py"
    labels = {
        "data_dir": "Root data directory",
        "tasks_db": "Workflow/task database",
        "memory_db": "Memory database",
        "threads_db": "Thread metadata and checkpoints",
        "logs_dir": "Application logs",
    }
    return [
        {
            "id": key,
            "title": title,
            "description": "Resolved under ROW_BOT_DATA_DIR when set, otherwise the default Row-Bot user data directory.",
            "source": repo_path(ROOT, path),
            "environment_override": "ROW_BOT_DATA_DIR",
        }
        for key, title in labels.items()
    ]


def collect_safety() -> list[dict[str, Any]]:
    approval_path = ROOT / "src" / "row_bot" / "approval_policy.py"
    labels = _assigned_dict(approval_path, "APPROVAL_MODE_LABELS")
    rows = []
    for mode, label in sorted(labels.items()):
        rows.append(
            {
                "id": str(mode),
                "title": str(label),
                "description": _approval_description(str(mode)),
                "source": repo_path(ROOT, approval_path),
                "decision": "block" if mode == "block" else "allow" if mode == "allow_all" else "ask",
            }
        )
    rows.append(
        {
            "id": "mcp-destructive-tools",
            "title": "MCP destructive tool classification",
            "description": "External MCP tools that look destructive require approval by default.",
            "source": "src/row_bot/mcp_client/safety.py",
            "decision": "ask",
        }
    )
    return rows


def _approval_description(mode: str) -> str:
    if mode == "block":
        return "Read-only or blocked mode for actions that would change external state."
    if mode == "allow_all":
        return "Automatically allows actions in trusted contexts."
    return "Prompts the user before a sensitive action continues."


def collect_docs_pages() -> list[dict[str, Any]]:
    docs_root = ROOT / "docs-site" / "docs"
    pages: list[dict[str, Any]] = []
    if not docs_root.exists():
        return pages
    paths = sorted(docs_root.rglob("*.md")) + sorted(docs_root.rglob("*.mdx"))
    for path in paths:
        rel = path.relative_to(docs_root)
        meta = _frontmatter(path)
        title = meta.get("title") or path.stem.replace("-", " ").title()
        pages.append(
            to_jsonable(
                DocsPageRecord(
                    id=slugify(str(rel.with_suffix(""))),
                    source=repo_path(ROOT, path),
                    path=str(rel).replace("\\", "/"),
                    route=public_route_for_doc(path, docs_root),
                    title=str(title),
                    description=str(meta.get("description") or ""),
                )
            )
        )
    return pages


def collect_version() -> dict[str, str]:
    version_file = ROOT / "src" / "row_bot" / "version.py"
    text = _read_text(version_file) if version_file.exists() else ""
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text)
    return {"version": match.group(1) if match else "unknown"}


def collect_metadata() -> dict[str, Any]:
    return {
        "ui_surfaces": _load_yaml(ROOT / "docs-content" / "metadata" / "ui_surfaces.yml"),
        "real_ui_surfaces": _load_yaml(ROOT / "docs-content" / "metadata" / "real_ui_surfaces.yml"),
        "settings": _load_yaml(ROOT / "docs-content" / "metadata" / "settings.yml"),
        "settings_tabs": _load_yaml(ROOT / "docs-content" / "metadata" / "settings_tabs.yml"),
        "home_tabs": _load_yaml(ROOT / "docs-content" / "metadata" / "home_tabs.yml"),
        "dialogs": _load_yaml(ROOT / "docs-content" / "metadata" / "dialogs.yml"),
        "docs_routes": _load_yaml(ROOT / "docs-content" / "metadata" / "docs_routes.yml"),
        "screenshots": _load_yaml(ROOT / "docs-content" / "metadata" / "screenshots.yml"),
        "how_to_guides": _load_yaml(ROOT / "docs-content" / "metadata" / "how_to_guides.yml"),
    }


def build_inventory() -> dict[str, Any]:
    bundled_skills = collect_skills("bundled_skills")
    tool_guides = collect_skills("tool_guides")
    return {
        "version": collect_version(),
        "tools": collect_tools(),
        "providers": collect_providers(),
        "settings": collect_settings(),
        "home_tabs": collect_home_tabs(),
        "channels": collect_channels(),
        "skills": bundled_skills + tool_guides,
        "mcp": collect_mcp(),
        "plugins": collect_plugins(),
        "data_paths": collect_data_paths(),
        "safety": collect_safety(),
        "docs_pages": collect_docs_pages(),
        "metadata": collect_metadata(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Row-Bot public docs inventory")
    parser.add_argument("--out", default="docs-build/inventory", help="Output directory")
    args = parser.parse_args()

    out_dir = (ROOT / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    inventory = build_inventory()
    write_json(out_dir / "inventory.json", inventory)
    for key, value in inventory.items():
        write_json(out_dir / f"{key}.json", value)
    print(f"Wrote public docs inventory to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
