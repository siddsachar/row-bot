"""Enforce the initial headless import and public type-annotation boundaries.

This scoped ratchet does not pretend to type-check the untyped legacy graph.
DTO value/schema conformance is tested by the protocol contract suite.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCOPES = ("application", "runtime", "projection", "api")
FORBIDDEN_IMPORTS = ("nicegui", "webview", "row_bot.ui", "row_bot.app",
                     "row_bot.developer.ui", "row_bot.designer.editor")
# The legacy NiceGUI UI is being removed: nothing outside it may import it.
PRESENTATION_IMPORTS = ("nicegui", "row_bot.ui")
PRESENTATION_PREFIXES = ("row_bot.plugins.ui_",)
LEGACY_PRESENTATION = (
    "ui/", "developer/ui.py", "skills_hub/ui.py",
    "plugins/ui_settings.py", "plugins/ui_marketplace.py", "plugins/ui_plugin_dialog.py",
    "designer/brand_dialog.py", "designer/editor.py", "designer/export_dialog.py", "designer/home_tab.py",
    "designer/import_dialog.py", "designer/presentation.py", "designer/review_dialog.py",
    "designer/share_dialog.py", "designer/template_gallery.py", "designer/thumbnail.py",
    "designer/page_navigator.py", "designer/preview.py", "designer/brand_lint.py",
    "designer/command_palette.py", "channels/whatsapp.py",
)


@dataclass(frozen=True)
class Finding:
    line: int
    code: str
    message: str


def inspect_source(source: str) -> list[Finding]:
    """Report concrete layer violations and unannotated public boundary methods."""
    tree = ast.parse(source)
    findings = []
    for node in ast.walk(tree):
        imports = []
        if isinstance(node, ast.Import):
            imports = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports = [node.module or ""]
            imports.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            if name in {"__import__", "import_module"} and node.args and isinstance(node.args[0], ast.Constant):
                imports = [str(node.args[0].value)]
        for module in imports:
            if any(module == forbidden or module.startswith(forbidden + ".") for forbidden in FORBIDDEN_IMPORTS):
                findings.append(Finding(node.lineno, "CP001", "Headless boundary imports presentation code"))
                break
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
            continue
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        arguments.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None)
        missing = [arg.arg for arg in arguments if arg.arg not in {"self", "cls"} and arg.annotation is None]
        if missing or node.returns is None:
            findings.append(Finding(node.lineno, "CP002", f"Public boundary {node.name} needs parameter/return annotations"))
    return findings


def _imported_modules(tree: ast.AST, package: str) -> list[tuple[int, str]]:
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*parent, *([base] if base else [])])
            modules.append((node.lineno, base))
            modules.extend((node.lineno, f"{base}.{alias.name}") for alias in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            if name in {"__import__", "import_module"} and node.args and isinstance(node.args[0], ast.Constant):
                modules.append((node.lineno, str(node.args[0].value)))
    return modules


def presentation_violations() -> list[str]:
    """Modules outside the legacy NiceGUI UI that import it."""
    source_root = ROOT / "src" / "row_bot"
    violations = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        if relative.startswith(LEGACY_PRESENTATION):
            continue
        package = ".".join(("row_bot", *path.relative_to(source_root).parent.parts))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, module in _imported_modules(tree, package):
            if (any(module == name or module.startswith(name + ".") for name in PRESENTATION_IMPORTS)
                    or module.startswith(PRESENTATION_PREFIXES)):
                violations.append(f"src/row_bot/{relative}:{line}: CP003 imports the legacy NiceGUI UI ({module})")
                break
    return violations


def boundary_paths() -> list[Path]:
    paths = [path for scope in SCOPES for path in (ROOT / "src" / "row_bot" / scope).rglob("*.py")]
    paths.extend(ROOT / "src" / "row_bot" / path for path in (
        "conversation_resources.py", "file_context.py", "message_projection.py", "developer/inspector_snapshot.py",
    ))
    return sorted(path for path in paths if path.is_file())


def main() -> int:
    count = 0
    paths = boundary_paths()
    for path in paths:
        for finding in inspect_source(path.read_text(encoding="utf-8")):
            count += 1
            print(f"{path.relative_to(ROOT).as_posix()}:{finding.line}: {finding.code} {finding.message}")
    for violation in presentation_violations():
        count += 1
        print(violation)
    print(f"Client platform boundary ratchet: {len(paths)} files, {count} violations")
    return int(count > 0)


if __name__ == "__main__":
    sys.exit(main())
