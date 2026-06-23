from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest


class MemoryKeyring:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        if self.fail:
            raise RuntimeError("keyring unavailable")
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        if self.fail:
            raise RuntimeError("keyring unavailable")
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        if self.fail:
            raise RuntimeError("keyring unavailable")
        self.values.pop((service, account), None)


@pytest.fixture
def plugin_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, object]]:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))

    import row_bot.secret_store as secret_store
    from row_bot.plugins import installer, loader, marketplace, registry, state

    secret_store._set_backend_for_tests(MemoryKeyring())

    modules = {
        "state": importlib.reload(state),
        "registry": importlib.reload(registry),
        "loader": importlib.reload(loader),
        "installer": importlib.reload(installer),
        "marketplace": importlib.reload(marketplace),
    }
    modules["registry"]._reset()
    modules["state"]._reset()
    modules["loader"]._reset()
    modules["marketplace"]._reset()

    try:
        yield modules
    finally:
        modules["registry"]._reset()
        modules["state"]._reset()
        modules["loader"]._reset()
        modules["marketplace"]._reset()
        secret_store._set_backend_for_tests(None)


def manifest_payload(plugin_id: str = "sample-plugin", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": plugin_id,
        "name": plugin_id.replace("-", " ").title(),
        "version": "1.0.0",
        "min_row_bot_version": "0.0.0",
        "author": {"name": "Tester", "github": "tester"},
        "description": "A deterministic test plugin.",
        "provides": {
            "tools": [{"name": "sample_tool", "display_name": "Sample Tool"}],
            "skills": [],
        },
        "settings": {},
        "python_dependencies": [],
    }
    payload.update(overrides)
    return payload


def write_plugin(
    root: Path,
    plugin_id: str = "sample-plugin",
    *,
    manifest: dict[str, object] | None = None,
    main: str | None = None,
    skill: str | None = None,
) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(manifest or manifest_payload(plugin_id)),
        encoding="utf-8",
    )
    (plugin_dir / "plugin_main.py").write_text(
        main
        or (
            "from plugins.api import PluginTool\n\n"
            "class SampleTool(PluginTool):\n"
            "    @property\n"
            "    def name(self): return 'sample_tool'\n"
            "    @property\n"
            "    def display_name(self): return 'Sample Tool'\n"
            "    @property\n"
            "    def description(self): return 'A deterministic plugin tool'\n"
            "    def execute(self, query: str) -> str:\n"
            "        return f'sample:{query}'\n\n"
            "def register(api):\n"
            "    api.register_tool(SampleTool(api))\n"
        ),
        encoding="utf-8",
    )
    if skill is not None:
        skill_dir = plugin_dir / "skills" / "sample_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(skill, encoding="utf-8")
    return plugin_dir
