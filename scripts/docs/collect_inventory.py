"""Collect public-docs inventory from Row-Bot source files.

The inventory intentionally avoids importing the application or running the
React client. It reads stable source locations (Python modules, the React
settings and Home models, and docs metadata) and emits deterministic JSON that
generated docs can consume. Sources are recorded as repository paths, with a
symbol or anchor where useful, never as line numbers.
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

from scripts.docs.schemas import (  # noqa: E402
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


class _TsLiteral:
    """Read one plain TypeScript literal starting at ``pos``.

    Objects, arrays, quoted strings, numbers, ``true``, ``false`` and ``null``
    are accepted. Anything else (spreads, calls, identifiers, template strings)
    raises ``ValueError`` so a model that stops being plain data fails loudly
    instead of producing a partial inventory.
    """

    _SPACE = re.compile(r"(?:\s+|//[^\n]*|/\*.*?\*/)+", re.DOTALL)
    _IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
    _NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
    _ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}

    def __init__(self, text: str, pos: int) -> None:
        self.text = text
        self.pos = pos

    def _peek(self) -> str:
        match = self._SPACE.match(self.text, self.pos)
        if match:
            self.pos = match.end()
        return self.text[self.pos : self.pos + 1]

    def _fail(self, expected: str) -> ValueError:
        line = self.text.count("\n", 0, self.pos) + 1
        found = self.text[self.pos : self.pos + 24].split("\n", 1)[0]
        return ValueError(f"expected {expected} on line {line}, found {found!r}")

    def value(self) -> Any:
        char = self._peek()
        if char == "{":
            return self._object()
        if char == "[":
            return self._array()
        if char in {"'", '"'}:
            return self._string()
        number = self._NUMBER.match(self.text, self.pos)
        if number:
            self.pos = number.end()
            return float(number.group()) if "." in number.group() else int(number.group())
        word = self._IDENT.match(self.text, self.pos)
        if word and word.group() in {"true", "false", "null"}:
            self.pos = word.end()
            return {"true": True, "false": False, "null": None}[word.group()]
        raise self._fail("a plain literal")

    def _string(self) -> str:
        quote = self.text[self.pos]
        chars: list[str] = []
        index = self.pos + 1
        while index < len(self.text):
            char = self.text[index]
            if char == quote:
                self.pos = index + 1
                return "".join(chars)
            if char == "\n":
                break
            if char == "\\":
                escaped = self.text[index + 1 : index + 2]
                if escaped == "u":
                    chars.append(chr(int(self.text[index + 2 : index + 6], 16)))
                    index += 6
                    continue
                chars.append(self._ESCAPES.get(escaped, escaped))
                index += 2
                continue
            chars.append(char)
            index += 1
        raise self._fail("a closing quote")

    def _array(self) -> list[Any]:
        self.pos += 1
        items: list[Any] = []
        while self._peek() != "]":
            items.append(self.value())
            if self._peek() == ",":
                self.pos += 1
            elif self._peek() != "]":
                raise self._fail("',' or ']'")
        self.pos += 1
        return items

    def _object(self) -> dict[str, Any]:
        self.pos += 1
        data: dict[str, Any] = {}
        while self._peek() != "}":
            if self._peek() in {"'", '"'}:
                key = self._string()
            else:
                word = self._IDENT.match(self.text, self.pos)
                if word is None:
                    raise self._fail("a property name")
                key = word.group()
                self.pos = word.end()
            if self._peek() != ":":
                raise self._fail(f"':' after {key!r}")
            self.pos += 1
            data[key] = self.value()
            if self._peek() == ",":
                self.pos += 1
            elif self._peek() != "}":
                raise self._fail("',' or '}'")
        self.pos += 1
        return data


def _ts_const(path: Path, name: str) -> Any:
    """Return the literal assigned to ``const <name>`` in a TypeScript file."""

    rel = repo_path(ROOT, path)
    text = _read_text(path)
    match = re.search(rf"\bconst\s+{re.escape(name)}\b[^=;]*=\s*", text)
    if match is None:
        raise ValueError(f"{rel}: `const {name}` not found")
    try:
        return _TsLiteral(text, match.end()).value()
    except ValueError as exc:
        raise ValueError(f"{rel}: `const {name}` is not a plain literal: {exc}") from exc


def _require_same_ids(what: str, expected: list[str], documented: dict[str, Any]) -> None:
    missing = [item for item in expected if item not in documented]
    unknown = sorted(set(documented) - set(expected))
    problems = []
    if missing:
        problems.append("missing " + ", ".join(missing))
    if unknown:
        problems.append("unknown " + ", ".join(unknown))
    if problems:
        raise ValueError(f"{what} does not match the React client: " + "; ".join(problems))


REACT_BASE = "/app-v2"


def _settings_model_path() -> Path:
    return ROOT / "frontend" / "src" / "features" / "settings" / "model.ts"


def _home_view_path() -> Path:
    return ROOT / "frontend" / "src" / "features" / "shell" / "Home.tsx"


def _settings_model() -> dict[str, Any]:
    """Read the React settings navigation: groups, page labels, keywords and rows."""

    path = _settings_model_path()
    rel = repo_path(ROOT, path)
    groups = _ts_const(path, "settingsGroups")
    labels = _ts_const(path, "leafLabels")
    keywords = _ts_const(path, "settingsKeywords")
    rows = _ts_const(path, "settingsRows")
    if not isinstance(groups, list) or not groups:
        raise ValueError(f"{rel}: settingsGroups is empty")
    leaves: list[tuple[dict[str, Any], str]] = []
    for group in groups:
        if not (
            isinstance(group, dict)
            and isinstance(group.get("id"), str)
            and isinstance(group.get("label"), str)
            and isinstance(group.get("leaves"), list)
            and group["leaves"]
        ):
            raise ValueError(f"{rel}: settings group {group!r} needs an id, a label and pages")
        leaves.extend((group, str(leaf)) for leaf in group["leaves"])
    leaf_ids = [leaf for _group, leaf in leaves]
    if len(set(leaf_ids)) != len(leaf_ids):
        raise ValueError(f"{rel}: a settings page belongs to more than one group")
    if not isinstance(labels, dict):
        raise ValueError(f"{rel}: leafLabels is not an object")
    _require_same_ids(f"{rel} leafLabels", leaf_ids, labels)
    if not isinstance(keywords, dict):
        raise ValueError(f"{rel}: settingsKeywords is not an object")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{rel}: settingsRows is empty")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not (
            isinstance(row, dict)
            and row.get("leaf") in labels
            and isinstance(row.get("anchor"), str)
            and row["anchor"]
            and isinstance(row.get("label"), str)
            and row["label"]
        ):
            raise ValueError(f"{rel}: settings row {row!r} needs a known page, an anchor and a label")
        key = (row["leaf"], row["anchor"])
        if key in seen:
            raise ValueError(f"{rel}: settings row {key[0]}#{key[1]} is listed twice")
        seen.add(key)
    return {"source": rel, "leaves": leaves, "labels": labels, "keywords": keywords, "rows": rows}


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
                "approval": _tool_approval_summary(path),
            }
        )
    return tools


def _tool_approval_summary(path: Path) -> str:
    tree = _parse_ast(path)
    if tree is None:
        return "Operation-dependent"
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != "destructive_tool_names":
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                names.add(child.value)
    if names:
        return "Approval-gated: " + ", ".join(sorted(names))
    if "approval" in _read_text(path).casefold():
        return "Operation-dependent approval"
    return "No tool-wide destructive classification"


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
                "route": _provider_route(provider_id, data),
                "experimental": bool(data.get("experimental")),
            }
        )
    return providers


def _provider_route(provider_id: str, data: dict[str, Any]) -> str:
    if provider_id == "ollama":
        return "Local"
    if str(data.get("risk_label") or "") == "subscription":
        return "Subscription"
    if provider_id == "custom":
        return "Custom endpoint"
    return "API"


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
    """One row per React settings page, in navigation order."""

    model = _settings_model()
    pages = _load_yaml(ROOT / "docs-content" / "metadata" / "settings.yml").get("pages", {})
    if not isinstance(pages, dict):
        raise ValueError("docs-content/metadata/settings.yml pages must be a mapping")
    _require_same_ids(
        "docs-content/metadata/settings.yml pages",
        [leaf for _group, leaf in model["leaves"]],
        pages,
    )
    rows: list[dict[str, Any]] = []
    for group, leaf in model["leaves"]:
        meta = pages.get(leaf) if isinstance(pages.get(leaf), dict) else {}
        rows.append(
            {
                "id": leaf,
                "title": str(model["labels"][leaf]),
                "category": str(group["label"]),
                "description": str(meta.get("description") or ""),
                "keywords": str(model["keywords"].get(leaf) or ""),
                "app_route": f"{REACT_BASE}/settings/{leaf}",
                "docs_route": str(meta.get("docs_route") or ""),
                "screenshot_id": str(meta.get("screenshot_id") or ""),
                "dependencies": str(meta.get("dependencies") or ""),
                "security": str(meta.get("security") or ""),
                "source": f"{model['source']}#{leaf}",
            }
        )
    return rows


def _call_keyword(node: ast.Call, name: str) -> ast.AST | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _control_value(node: ast.Call, keyword: str) -> Any:
    value_node = _call_keyword(node, keyword)
    if value_node is None:
        return ""
    value = _literal_value(value_node)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value if value is not None else ""
    if isinstance(value, (list, dict)):
        return value
    return "Configured value"


def collect_settings_controls() -> list[dict[str, Any]]:
    """One row per searchable React settings row (``settingsRows``)."""

    model = _settings_model()
    pages = {page["id"]: page for page in collect_settings()}
    rows: list[dict[str, Any]] = []
    for row in model["rows"]:
        page = pages[row["leaf"]]
        anchor = str(row["anchor"])
        app_route = page["app_route"]
        context = row.get("context")
        if context in {"skills", "plugins", "mcp"}:
            kind = {"skills": "skill", "plugins": "plugin", "mcp": "mcp"}[context]
            discover = anchor in {"public-skills", "plugin-marketplace", "mcp-marketplace"}
            app_route += "?tab=" + ("discover" if discover else "my") + "&type=" + kind
            if discover:
                app_route += "&source=" + {"skills": "clawhub", "plugins": "native", "mcp": "official"}[context]
        rows.append(
            {
                "id": f"{page['id']}-{slugify(anchor)}",
                "page_id": page["id"],
                "page": page["title"],
                "category": page["category"],
                "label": str(row["label"]),
                "anchor": anchor,
                "keywords": str(row.get("keywords") or ""),
                "app_route": f"{app_route}#{anchor}",
                "docs_route": page["docs_route"],
                "source": f"{model['source']}#{anchor}",
            }
        )
    ids = [row["id"] for row in rows]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError("settings rows share an id: " + ", ".join(duplicates))
    return rows


def _cli_rows_for_path(
    path: Path,
    *,
    command_by_receiver: dict[str, str] | None = None,
    command_by_function: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    tree = _parse_ast(path)
    if tree is None:
        return []
    parser_commands: dict[str, str] = {"parser": "row-bot"}
    parser_commands.update(command_by_receiver or {})
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_parser" and node.value.args):
            continue
        command = _literal_value(node.value.args[0])
        if isinstance(command, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    parser_commands.setdefault(target.id, f"row-bot plugin {command}")
    rows: list[dict[str, Any]] = []
    function_commands = command_by_function or {}
    for parent in ast.walk(tree):
        if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        function_command = function_commands.get(parent.name)
        for node in parent.body:
            for candidate in ast.walk(node):
                if not isinstance(candidate, ast.Call) or not isinstance(candidate.func, ast.Attribute) or candidate.func.attr != "add_argument":
                    continue
                receiver = candidate.func.value.id if isinstance(candidate.func.value, ast.Name) else "parser"
                options = [value for value in (_literal_value(arg) for arg in candidate.args) if isinstance(value, str)]
                if not options:
                    continue
                description = str(_control_value(candidate, "help") or "")
                if description == "SUPPRESS":
                    continue
                rows.append(
                    {
                        "id": slugify(
                            "-".join(
                                (
                                    function_command or parser_commands.get(receiver, "row-bot"),
                                    *options,
                                )
                            )
                        ),
                        "command": (function_command or parser_commands.get(receiver, "row-bot")),
                        "option": ", ".join(options),
                        "description": description,
                        "default": _control_value(candidate, "default"),
                        "source": repo_path(ROOT, path),
                    }
                )
    return rows


def collect_cli_options() -> list[dict[str, Any]]:
    rows = _cli_rows_for_path(ROOT / "src" / "row_bot" / "launcher.py")
    access_path = ROOT / "src" / "row_bot" / "access" / "cli.py"
    rows.extend(
        _cli_rows_for_path(
            access_path,
            command_by_receiver={
                "parser": "row-bot access",
                "invite": "row-bot access invite",
                "lifetime": "row-bot access invite",
                "list_parser": "row-bot access list",
                "revoke": "row-bot access revoke",
                "revoke_all": "row-bot access revoke-all",
                "doctor": "row-bot access doctor",
            },
            command_by_function={
                "configure_serve_parser": "row-bot serve",
                "_add_access_common": "row-bot access subcommands",
            },
        )
    )
    access_commands = (
        "row-bot access invite",
        "row-bot access list",
        "row-bot access revoke",
        "row-bot access revoke-all",
        "row-bot access doctor",
    )
    expanded_rows: list[dict[str, Any]] = []
    for row in rows:
        if row["command"] != "row-bot access subcommands":
            expanded_rows.append(row)
            continue
        for command in access_commands:
            expanded = dict(row)
            expanded["command"] = command
            expanded["id"] = slugify(f"{command}-{row['option']}")
            expanded_rows.append(expanded)
    unique_rows = {(row["command"], row["option"], row["source"]): row for row in expanded_rows}
    return sorted(
        unique_rows.values(),
        key=lambda row: (row["command"], row["option"], row["source"]),
    )


def collect_environment() -> list[dict[str, Any]]:
    variables = {
        "ROW_BOT_DATA_DIR": "Override the local Row-Bot data directory for this process.",
        "ROW_BOT_WORKSPACE": "Override the default workspace used by file-oriented tools.",
        "ROW_BOT_HOST": "Choose the interface bound by the local application server.",
        "ROW_BOT_PORT": "Choose the preferred local application port.",
        "ROW_BOT_DEPLOYMENT_MODE": "Choose desktop or authenticated server request policy.",
        "ROW_BOT_PUBLIC_URL": "Set the canonical browser-facing origin for server mode.",
        "ROW_BOT_PUBLIC_ORIGINS": "Set one or more allowed browser-facing origins.",
        "ROW_BOT_ALLOWED_HOSTS": "Allow exact HTTP Host authorities accepted by the access gate.",
        "ROW_BOT_TRUSTED_PROXY_CIDRS": "Trust forwarding headers only from exact connecting proxy networks.",
        "ROW_BOT_UNTRUSTED_FORWARDED_ACTION": "Reject or ignore forwarding headers received from an untrusted peer.",
        "ROW_BOT_WORKERS": "Set the server worker count; Row-Bot requires exactly one.",
        "ROW_BOT_EPHEMERAL_DATA": "Tell access diagnostics that server data is intentionally ephemeral.",
        "ROW_BOT_SECRETS_DIR": "Read allowlisted externally managed secrets from individual files.",
        "ROW_BOT_BROWSER_HEADLESS": "Run bundled browser automation without an interactive display.",
        "ROW_BOT_NATIVE": "Select native-window behaviour for advanced launch scenarios.",
        "ROW_BOT_AUTO_START_OLLAMA": "Allow or suppress launcher attempts to start Ollama automatically.",
        "ROW_BOT_STARTUP_TIMEOUT": "Set the launcher startup timeout in seconds.",
        "ROW_BOT_WEBVIEW_STORAGE_PATH": "Override native webview storage for advanced troubleshooting.",
        "ROW_BOT_INSTALL_ROOT": "Identify the installed application root for updater and repair flows.",
        "ROW_BOT_PLUGIN_INDEX_URL": "Override the public plugin marketplace index URL.",
        "ROW_BOT_PLUGIN_REPO_URL": "Override the plugin repository used by marketplace installs.",
        "ROW_BOT_XAI_OAUTH_CLIENT_ID": "Override the xAI OAuth client identifier.",
        "ROW_BOT_XAI_OAUTH_REDIRECT_PORT": "Override the local xAI OAuth callback port.",
        "ROW_BOT_XAI_OAUTH_SCOPES": "Override the requested xAI OAuth scopes.",
        "ROW_BOT_REALTIME_INSTRUCTIONS": "Override additional realtime voice session instructions.",
    }
    source_roots = [ROOT / "src" / "row_bot"]
    source_by_variable: dict[str, list[str]] = {name: [] for name in variables}
    for source_root in source_roots:
        for path in sorted(source_root.rglob("*.py")):
            text = _read_text(path)
            for name in variables:
                # Keep canonical *_ENV aliases, but not longer identifiers such
                # as the native bridge global __ROW_BOT_NATIVE_CLIENT__.
                if name in text and re.search(rf"\b{re.escape(name)}(?:_ENV)?\b", text):
                    source_by_variable[name].append(repo_path(ROOT, path))
    return [
        {
            "id": slugify(name),
            "variable": name,
            "description": description,
            "source": ", ".join(source_by_variable[name]),
        }
        for name, description in variables.items()
    ]


def collect_home_tabs() -> list[dict[str, Any]]:
    """One row per React Home tab (``homeTabs``), in the order Home shows them."""

    path = _home_view_path()
    rel = repo_path(ROOT, path)
    tab_ids = _ts_const(path, "homeTabs")
    if not isinstance(tab_ids, list) or not tab_ids or not all(
        isinstance(tab, str) and tab for tab in tab_ids
    ):
        raise ValueError(f"{rel}: homeTabs must be a non-empty list of tab ids")
    tabs = _load_yaml(ROOT / "docs-content" / "metadata" / "home_tabs.yml").get("tabs", {})
    if not isinstance(tabs, dict):
        raise ValueError("docs-content/metadata/home_tabs.yml tabs must be a mapping")
    _require_same_ids("docs-content/metadata/home_tabs.yml tabs", tab_ids, tabs)
    rows: list[dict[str, Any]] = []
    for tab in tab_ids:
        meta = tabs[tab] if isinstance(tabs[tab], dict) else {}
        rows.append(
            {
                "id": tab,
                "title": str(meta.get("title") or ""),
                "app_route": f"{REACT_BASE}/?tab={tab}",
                "docs_route": str(meta.get("docs_route") or ""),
                "screenshot_id": str(meta.get("screenshot_id") or ""),
                "source": str(meta.get("source") or ""),
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
            "source": "src/row_bot/developer/tool_capsules.py",
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
        "settings": _load_yaml(ROOT / "docs-content" / "metadata" / "settings.yml"),
        "home_tabs": _load_yaml(ROOT / "docs-content" / "metadata" / "home_tabs.yml"),
        "dialogs": _load_yaml(ROOT / "docs-content" / "metadata" / "dialogs.yml"),
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
        "settings_controls": collect_settings_controls(),
        "cli_options": collect_cli_options(),
        "environment": collect_environment(),
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
