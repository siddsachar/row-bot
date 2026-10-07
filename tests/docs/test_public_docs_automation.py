import json
import re
from contextlib import ExitStack
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import yaml
import pytest

from scripts.docs.collect_inventory import ROOT, build_inventory
from scripts.docs.generate_llms_txt import generate
from scripts.docs.generate_mdx import check_pages, render_pages
from scripts.docs.sync_github_pages import (
    HAND_CURATED_MARKETING_FILES,
    HAND_CURATED_PUBLISHED_FILES,
    check_sync,
    sync,
)
from scripts.docs.validate_public_docs import validate


class _PublishedLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "a" and values.get("href"):
            self.hrefs.append(values["href"])


def test_public_docs_inventory_has_core_sections() -> None:
    inventory = build_inventory()

    assert inventory["version"]["version"] != "unknown"
    assert any(tool["id"] == "browser" for tool in inventory["tools"])
    assert any(provider["id"] == "ollama" for provider in inventory["providers"])
    assert any(tab["title"] == "Providers" for tab in inventory["settings"])
    assert any(tab["title"] == "Workflows" for tab in inventory["home_tabs"])
    assert any(channel["id"] == "telegram" for channel in inventory["channels"])
    assert any(skill["id"] == "task_automation" for skill in inventory["skills"])
    assert any(server["id"] == "microsoft-playwright" for server in inventory["mcp"])
    assert any(plugin["id"] == "plugin-manifest" for plugin in inventory["plugins"])
    assert any(path["id"] == "threads_db" for path in inventory["data_paths"])
    assert any(rule["id"] == "approve" for rule in inventory["safety"])
    assert any(page["path"] == "index.mdx" for page in inventory["docs_pages"])
    controls = inventory["settings_controls"]
    assert {row["page_id"] for row in controls} <= {page["id"] for page in inventory["settings"]}
    assert all(
        row["app_route"] == f"/app-v2/settings/{row['page_id']}#{row['anchor']}"
        and row["source"] == f"frontend/src/features/settings/model.ts#{row['anchor']}"
        for row in controls
    )
    assert inventory["cli_options"]
    assert inventory["environment"]


def test_inventory_sources_never_carry_line_numbers() -> None:
    inventory = build_inventory()
    sources = [
        str(row.get("source") or "")
        for section in inventory.values()
        if isinstance(section, list)
        for row in section
        if isinstance(row, dict)
    ]

    assert len(sources) > 100
    assert [source for source in sources if re.search(r"\.\w+:\d+", source)] == []
    assert all(row["source"] == "src/row_bot/launcher.py" for row in inventory["cli_options"] if row["command"] == "row-bot")


def _write_react_client(root: Path, model: str, settings: str, home: str, home_tabs: str) -> None:
    files = {
        "frontend/src/features/settings/model.ts": model,
        "docs-content/metadata/settings.yml": settings,
        "frontend/src/features/shell/Home.tsx": home,
        "docs-content/metadata/home_tabs.yml": home_tabs,
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


_MODEL = """
export const settingsGroups = [
  { id: 'general', label: 'General', leaves: ['preferences'] },
  // A trailing comment and comma must not matter.
  { id: 'system', label: 'System', leaves: ['access'], },
] as const;
const leafLabels: Record<SettingsLeafId, string> = {
  preferences: 'Preferences',
  access: "Devices & remote access",
};
export const settingsKeywords: Record<SettingsLeafId, string> = {
  preferences: 'identity',
  'access': 'phone qr',
};
export const settingsRows: SettingsRow[] = [
  { leaf: 'preferences', anchor: 'identity.name', label: 'Assistant name' },
  /* block comment */
  { leaf: 'access', anchor: 'devices', label: 'Your devices', keywords: 'sessions' },
];
"""
_SETTINGS = """
pages:
  preferences: {description: Identity., docs_route: /docs/settings/preferences}
  access: {description: Devices., docs_route: /docs/operations/remote-access, security: Owner access.}
"""
_HOME = "const homeTabs = ['overview', 'monitor'];\nexport default function Home() {}\n"
_HOME_TABS = """
tabs:
  overview: {title: Overview, docs_route: /docs/home/, source: frontend/src/features/home/OverviewHome.tsx}
  monitor: {title: Monitor, docs_route: /docs/home/monitor, source: frontend/src/features/home/MonitorHome.tsx}
"""


def test_settings_and_home_inventory_read_the_react_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.docs.collect_inventory as collector

    _write_react_client(tmp_path, _MODEL, _SETTINGS, _HOME, _HOME_TABS)
    monkeypatch.setattr(collector, "ROOT", tmp_path)

    pages = collector.collect_settings()
    assert [(page["id"], page["title"], page["category"]) for page in pages] == [
        ("preferences", "Preferences", "General"),
        ("access", "Devices & remote access", "System"),
    ]
    assert pages[1]["app_route"] == "/app-v2/settings/access"
    assert pages[1]["security"] == "Owner access."
    assert collector.collect_settings_controls() == [
        {
            "id": "preferences-identity-name",
            "page_id": "preferences",
            "page": "Preferences",
            "category": "General",
            "label": "Assistant name",
            "anchor": "identity.name",
            "keywords": "",
            "app_route": "/app-v2/settings/preferences#identity.name",
            "docs_route": "/docs/settings/preferences",
            "source": "frontend/src/features/settings/model.ts#identity.name",
        },
        {
            "id": "access-devices",
            "page_id": "access",
            "page": "Devices & remote access",
            "category": "System",
            "label": "Your devices",
            "anchor": "devices",
            "keywords": "sessions",
            "app_route": "/app-v2/settings/access#devices",
            "docs_route": "/docs/operations/remote-access",
            "source": "frontend/src/features/settings/model.ts#devices",
        },
    ]
    assert [(tab["id"], tab["app_route"]) for tab in collector.collect_home_tabs()] == [
        ("overview", "/app-v2/?tab=overview"),
        ("monitor", "/app-v2/?tab=monitor"),
    ]


@pytest.mark.parametrize(
    ("model", "settings", "home_tabs", "message"),
    [
        (_MODEL.replace("export const settingsRows", "const renamedRows"), _SETTINGS, _HOME_TABS, "settingsRows` not found"),
        (_MODEL.replace("{ leaf: 'preferences', anchor: 'identity.name', label: 'Assistant name' },", "...extraRows,"), _SETTINGS, _HOME_TABS, "not a plain literal"),
        (_MODEL.replace("leaf: 'access', anchor", "leaf: 'gone', anchor"), _SETTINGS, _HOME_TABS, "needs a known page"),
        (_MODEL, _SETTINGS.replace("  access:", "  utilities:"), _HOME_TABS, "missing access; unknown utilities"),
        (_MODEL, _SETTINGS, _HOME_TABS.replace("  monitor:", "  designer:"), "missing monitor; unknown designer"),
    ],
)
def test_react_inventory_fails_loudly_when_the_client_and_metadata_disagree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str, settings: str, home_tabs: str, message: str,
) -> None:
    import scripts.docs.collect_inventory as collector

    _write_react_client(tmp_path, model, settings, _HOME, home_tabs)
    monkeypatch.setattr(collector, "ROOT", tmp_path)

    with pytest.raises(ValueError, match=re.escape(message)):
        collector.collect_settings_controls()
        collector.collect_home_tabs()


def test_progressive_tools_and_skills_are_documented_at_public_entry_points() -> None:
    guide = (ROOT / "docs-site" / "docs" / "guides" / "progressive-tools-and-skills.mdx").read_text(
        encoding="utf-8"
    )
    tools = (ROOT / "docs-site" / "docs" / "settings" / "tools.mdx").read_text(
        encoding="utf-8"
    )
    skills = (ROOT / "docs-site" / "docs" / "skills" / "index.mdx").read_text(
        encoding="utf-8"
    )
    docs_index = (ROOT / "docs-site" / "docs" / "index.mdx").read_text(encoding="utf-8")
    marketing = (ROOT / "docs" / "features.html").read_text(encoding="utf-8")
    generated_controls = (
        ROOT / "docs-site" / "docs" / "reference" / "generated" / "settings-controls.mdx"
    ).read_text(encoding="utf-8")

    for phrase in (
        "Auto-select external tools",
        "Load all external tools",
        "MCP servers, plugins, Custom Tools, and channels",
        "Up to five automatically selected skills",
        "cannot grant a tool that its profile denies",
        "real integration name",
    ):
        assert phrase in guide
    assert "progressive capability loading" in tools
    assert "parent task or child Agent" in skills
    assert "/docs/guides/progressive-tools-and-skills" in docs_index
    assert "Progressive external tools" in marketing
    assert "| Capability loading | external tools | `/app-v2/settings/tools#capability-loading` |" in generated_controls


def test_reasoning_controls_are_documented_at_public_entry_points() -> None:
    docs_root = ROOT / "docs-site" / "docs"
    guide = (docs_root / "chat" / "reasoning-controls.mdx").read_text(encoding="utf-8")
    chat = (docs_root / "chat" / "index.mdx").read_text(encoding="utf-8")
    models = (docs_root / "configuration" / "models-and-providers.mdx").read_text(
        encoding="utf-8"
    )
    channels = (docs_root / "integrations" / "channels.mdx").read_text(encoding="utf-8")
    sidebar = (ROOT / "docs-site" / "sidebars.ts").read_text(encoding="utf-8")

    for phrase in (
        "Provider default",
        "one thread and one exact provider-qualified model",
        "/reasoning budget 4096",
        "retries once with Provider default",
        "Settings -> Providers",
    ):
        assert phrase in guide
    assert "/docs/chat/reasoning-controls" in chat
    assert "/docs/chat/reasoning-controls" in models
    assert "`/reasoning`" in channels
    assert "'chat/reasoning-controls'" in sidebar


def test_all_published_html_internal_links_resolve() -> None:
    publish_root = ROOT / "docs"
    pages = sorted(publish_root.rglob("*.html"))
    parsed: dict[Path, _PublishedLinkParser] = {}

    def parse(path: Path) -> _PublishedLinkParser:
        if path not in parsed:
            parser = _PublishedLinkParser()
            parser.feed(path.read_text(encoding="utf-8"))
            parsed[path] = parser
        return parsed[path]

    for page in pages:
        for href in parse(page).hrefs:
            parts = urlsplit(href)
            if parts.scheme or parts.netloc or href.startswith(("mailto:", "tel:")):
                continue
            if parts.path.startswith("/"):
                target = publish_root / unquote(parts.path.lstrip("/"))
            else:
                target = page.parent / unquote(parts.path)
            if not parts.path:
                target = page
            if target.is_dir() or (not target.suffix and (target / "index.html").is_file()):
                target = target / "index.html"
            assert target.is_file(), f"{page.relative_to(publish_root)}: {href}"
            if parts.fragment:
                assert unquote(parts.fragment) in parse(target).ids, (
                    f"{page.relative_to(publish_root)}: {href}"
                )


def test_generated_mdx_pages_are_current() -> None:
    errors = check_pages(render_pages(build_inventory()))
    assert errors == []


def test_environment_inventory_requires_exact_names_or_env_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.docs.collect_inventory as collector

    source = tmp_path / "src" / "row_bot"
    source.mkdir(parents=True)
    (source / "literal.py").write_text(
        'value = getenv("ROW_BOT_NATIVE")\n# ROW_BOT_PORT=8081\n', encoding="utf-8",
    )
    (source / "aliases.py").write_text(
        'host = getenv(ROW_BOT_HOST_ENV)\nport = getenv(ROW_BOT_PORT_ENV)\n', encoding="utf-8",
    )
    (source / "unrelated.py").write_text(
        '__ROW_BOT_NATIVE_CLIENT__\nPRE_ROW_BOT_NATIVE\nROW_BOT_NATIVE_EXTRA\n'
        'ROW_BOT_NATIVELY\nROW_BOT_PORT_ENV_EXTRA\n', encoding="utf-8",
    )
    monkeypatch.setattr(collector, "ROOT", tmp_path)

    rows = {row["variable"]: row["source"] for row in collector.collect_environment()}
    assert rows["ROW_BOT_NATIVE"] == "src/row_bot/literal.py"
    assert rows["ROW_BOT_HOST"] == "src/row_bot/aliases.py"
    assert rows["ROW_BOT_PORT"] == "src/row_bot/aliases.py, src/row_bot/literal.py"


def test_generated_mdx_is_stable_after_inventory_json_round_trip() -> None:
    inventory = build_inventory()
    serialized = json.loads(json.dumps(inventory, sort_keys=True))

    assert render_pages(inventory) == render_pages(serialized)


def test_github_pages_sync_preserves_marketing_files(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    publish_dir = tmp_path / "publish"
    for name in ("assets", "docs", "img", "oauth", "pagefind", "search"):
        source = build_dir / name
        source.mkdir(parents=True)
        (source / "artifact.txt").write_text(name, encoding="utf-8")
    pagefind = build_dir / "pagefind"
    (pagefind / "fragment").mkdir()
    (pagefind / "index").mkdir()
    (pagefind / "pagefind.js").write_text("runtime", encoding="utf-8")
    (pagefind / "pagefind-ui.css").write_text("styles", encoding="utf-8")
    (pagefind / "wasm.unknown.pagefind").write_bytes(b"wasm")
    (pagefind / "fragment" / "source.pf_fragment").write_bytes(b"fragment")
    (pagefind / "index" / "source.pf_index").write_bytes(b"index")
    (pagefind / "pagefind.en_source.pf_meta").write_bytes(b"metadata")
    (pagefind / "pagefind-entry.json").write_text(
        json.dumps({"version": "1", "languages": {"en": {"hash": "source", "page_count": 1}}}),
        encoding="utf-8",
    )
    for name in ("llms-full.txt", "llms.txt", "sitemap.xml"):
        (build_dir / name).write_text(name, encoding="utf-8")
    publish_dir.mkdir()
    marketing_files = {}
    for index, name in enumerate(HAND_CURATED_MARKETING_FILES):
        marketing = publish_dir / name
        payload = f"marketing-{index}".encode()
        marketing.write_bytes(payload)
        marketing_files[marketing] = payload
    obsolete = publish_dir / "docs.html"
    obsolete.write_text("old route format", encoding="utf-8")
    curated_relative = HAND_CURATED_PUBLISHED_FILES[0]
    build_curated = build_dir / Path(*curated_relative.split("/"))
    build_curated.parent.mkdir(parents=True, exist_ok=True)
    build_curated.write_bytes(b"generated replacement")
    published_curated = publish_dir / Path(*curated_relative.split("/"))
    published_curated.parent.mkdir(parents=True, exist_ok=True)
    published_curated.write_bytes(b"approved hand-curated image")

    sync(build_dir, publish_dir)

    assert check_sync(build_dir, publish_dir) == []
    assert all(path.read_bytes() == payload for path, payload in marketing_files.items())
    assert published_curated.read_bytes() == b"approved hand-curated image"
    assert not obsolete.exists()
    published_entry = publish_dir / "pagefind" / "pagefind-entry.json"
    published_entry.write_text(
        json.dumps({"version": "1", "languages": {"en": {"hash": "linux", "page_count": 1}}}),
        encoding="utf-8",
    )
    assert check_sync(build_dir, publish_dir) == []
    (publish_dir / "docs" / "artifact.txt").write_text("stale", encoding="utf-8")
    assert check_sync(build_dir, publish_dir) == [
        f"Published directory is stale: {(publish_dir / 'docs').resolve()}"
    ]


def test_the_sign_in_client_metadata_is_published_at_the_address_it_names() -> None:
    path = Path("oauth") / "client-metadata.json"
    published = json.loads((ROOT / "docs" / path).read_text(encoding="utf-8"))
    assert published == json.loads((ROOT / "docs-site" / "static" / path).read_text(encoding="utf-8"))
    site = (ROOT / "docs" / "CNAME").read_text(encoding="utf-8").strip()
    assert published["client_id"] == f"https://{site}/{path.as_posix()}"  # A client metadata document names itself.
    assert published["token_endpoint_auth_method"] == "none"
    assert all(urlsplit(uri).hostname == "127.0.0.1" for uri in published["redirect_uris"])


def test_llms_txt_generation_covers_docs_routes(tmp_path: Path) -> None:
    docs_root = ROOT / "docs-site" / "docs"
    generate(docs_root, tmp_path)

    llms = (tmp_path / "llms.txt").read_text(encoding="utf-8")
    llms_full = (tmp_path / "llms-full.txt").read_text(encoding="utf-8")

    assert "# Row-Bot Docs" in llms
    assert "[Row-Bot Documentation](/docs/)" in llms
    assert "Route: /docs/reference/generated/tools" in llms
    assert "Route: /docs/" in llms_full
    assert (tmp_path / "docs" / "llms.txt").is_file()
    assert (tmp_path / "docs" / "llms-full.txt").is_file()
    for path in sorted(docs_root.rglob("*.mdx")) + sorted(docs_root.rglob("*.md")):
        from scripts.docs.schemas import public_route_for_doc

        assert public_route_for_doc(path, docs_root) in llms



def test_screenshot_manifest_is_real_ui_and_safe() -> None:
    import scripts.docs.capture_real_ui_screenshots as capture

    screenshots = capture._load_manifest()

    required = [shot for shot in screenshots.values() if shot["status"] == "required"]
    assert len(screenshots) >= 20
    assert len(required) >= 20
    assert all(shot["status"] in {"required", "deferred"} for shot in screenshots.values())
    assert all(
        shot.get("alt") and shot.get("title") and shot.get("output") and shot.get("docs_pages")
        for shot in screenshots.values()
    )
    assert all("/docs-mode/" not in shot.get("route", "") for shot in screenshots.values())
    # A capture target, when present, opens the React client; none may be partial.
    assert all(capture._capture_target_problems(shot) == [] for shot in screenshots.values())
    assert all(shot.get("source") in {"isolated-demo-data", "isolated-first-launch"} for shot in required)
    expected_dimensions = {"desktop": (3840, 2160), "wide": (3840, 2160), "mobile": (390, 844)}
    assert all(shot.get("viewport") in expected_dimensions for shot in required)
    home_knowledge = screenshots["home-knowledge"]
    assert home_knowledge["capture_policy"] == "hand-curated"
    assert home_knowledge["dimension_policy"] == "flexible"
    assert len(home_knowledge["curated_sha256"]) == 64


def test_hand_curated_home_screenshot_matches_both_approved_copies() -> None:
    import scripts.docs.capture_real_ui_screenshots as capture

    manifest = capture._load_manifest()
    shot = manifest["home-knowledge"]
    source = (
        ROOT
        / "docs-site"
        / "static"
        / "img"
        / "screenshots"
        / "real-ui"
        / "home-knowledge.png"
    )
    published = (
        ROOT
        / "docs"
        / "img"
        / "screenshots"
        / "real-ui"
        / "home-knowledge.png"
    )

    assert capture._validate_image(source, {"id": "home-knowledge", **shot}) == []
    assert capture._validate_image(published, {"id": "home-knowledge", **shot}) == []
    assert source.read_bytes() == published.read_bytes()


def test_capture_preserves_hand_curated_screenshot_without_opening_a_browser() -> None:
    import scripts.docs.capture_real_ui_screenshots as capture

    class BrowserMustNotOpen:
        def new_page(self, **_kwargs):
            raise AssertionError("hand-curated screenshots must not be recaptured")

    record = capture._capture_one(
        BrowserMustNotOpen(),
        0,
        "home-knowledge",
        {
            "title": "Knowledge Home tab",
            "output": "home-knowledge.png",
            "capture_policy": "hand-curated",
        },
    )

    assert record["status"] == "ok"
    assert record["preserved"] is True
    assert record["reason"] == "hand-curated asset preserved"


def test_mobile_screenshots_render_at_native_width() -> None:
    component = (ROOT / "docs-site" / "src" / "components" / "Screenshot.tsx").read_text(encoding="utf-8")
    styles = (ROOT / "docs-site" / "src" / "css" / "custom.css").read_text(encoding="utf-8")

    assert "id.startsWith('mobile-')" in component
    version = re.search(r'__version__ = "([^"]+)"',
                        (ROOT / "src" / "row_bot" / "version.py").read_text(encoding="utf-8")).group(1)
    assert f"const SCREENSHOT_REVISION = '{version}';" in component
    assert ".png?v=${SCREENSHOT_REVISION}" in component
    assert "rowBotScreenshotMobile" in component
    assert "width={isMobile ? 390 : undefined}" in component
    assert ".rowBotScreenshotMobile" in styles
    assert "width: min(100%, 390px);" in styles


def test_conceptual_guides_link_to_configuration_pages() -> None:
    concepts = ROOT / "docs-site" / "docs" / "concepts"
    expected = {
        "request-lifecycle.mdx": "/docs/configuration/models-and-providers",
        "memory-knowledge-and-dream-cycle.mdx": "/docs/settings/knowledge",
        "profiles-goals-and-agents.mdx": "/docs/profiles-goals-agents/",
        "background-workflows.mdx": "/docs/guides/workflows",
        "extensions-and-trust.mdx": "/docs/extending/",
    }

    for filename, configuration_route in expected.items():
        content = (concepts / filename).read_text(encoding="utf-8")
        assert configuration_route in content


def test_docs_navigation_returns_to_the_marketing_landing_page() -> None:
    config = (ROOT / "docs-site" / "docusaurus.config.ts").read_text(encoding="utf-8")
    docs_home = (ROOT / "docs-site" / "src" / "pages" / "index.tsx").read_text(
        encoding="utf-8"
    )

    assert "const landingPageUrl = 'https://row-bot.ai/';" in config
    assert config.count("href: landingPageUrl") == 3
    assert "{href: landingPageUrl, label: 'Home'" in config
    assert "label: 'Download'" in config
    assert "href: installationUrl" in config
    assert "const installationUrl = 'https://row-bot.ai/#install';" in config
    assert "https://row-bot.ai/features.html" in config
    assert "https://row-bot.ai/architecture.html" in config
    assert "https://row-bot.ai/contact.html" in config
    assert "github.com/siddsachar/row-bot/releases/latest" not in config
    assert 'href="https://row-bot.ai/#install"' in docs_home
    assert "github.com/siddsachar/row-bot/releases/latest" not in docs_home


def test_sitemap_source_includes_marketing_pages_without_shadow_copies() -> None:
    config = (ROOT / "docs-site" / "docusaurus.config.ts").read_text(encoding="utf-8")

    for url in (
        "https://row-bot.ai/features.html",
        "https://row-bot.ai/architecture.html",
        "https://row-bot.ai/contact.html",
    ):
        assert url in config
    assert "404.html" not in config
    assert not (ROOT / "docs-site" / "static" / "architecture.html").exists()
    assert not (ROOT / "docs-site" / "static" / "contact.html").exists()


def test_docs_ci_build_regenerates_llm_exports_before_building() -> None:
    package = json.loads(
        (ROOT / "docs-site" / "package.json").read_text(encoding="utf-8")
    )
    scripts = package["scripts"]

    assert scripts["generate:llms"] == (
        "python ../scripts/docs/generate_llms_txt.py "
        "--docs-root docs-site/docs --out-dir docs-site/static"
    )
    assert scripts["build:ci"].startswith("npm run generate:llms && ")


def test_docs_workflow_uses_the_canonical_build_container() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8")
    )
    build_job = workflow["jobs"]["build"]
    image = build_job["env"]["DOCS_NODE_IMAGE"]
    platform = build_job["env"]["DOCS_NODE_PLATFORM"]
    steps = build_job["steps"]
    build_step = next(
        step
        for step in steps
        if step["name"] == "Build and verify published docs in canonical container"
    )

    assert image == "node:20.20.2-bookworm"
    assert platform == "linux/amd64"
    assert not any(
        step.get("uses", "").startswith("actions/setup-node") for step in steps
    )
    assert "docker run --rm" in build_step["run"]
    assert '"$DOCS_NODE_PLATFORM"' in build_step["run"]
    assert '"$DOCS_NODE_IMAGE"' in build_step["run"]
    assert "npm ci && npm run build:ci" in build_step["run"]
    assert "sync_github_pages.py --check" in build_step["run"]
    readme = (ROOT / "docs-site" / "README.md").read_text(encoding="utf-8")
    assert image in readme
    assert f"--platform {platform}" in readme


def test_docs_build_inputs_have_canonical_line_endings() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    for pattern in (
        "docs-content/**/*.md",
        "docs-content/**/*.yml",
        "docs-site/**/*.css",
        "docs-site/**/*.js",
        "docs-site/**/*.json",
        "docs-site/**/*.md",
        "docs-site/**/*.mdx",
        "docs-site/**/*.ts",
        "docs-site/**/*.tsx",
        "docs/assets/**/*.js",
        "docs/docs/**/*.html",
        "docs/pagefind/**/*.js",
        "docs/search/**/*.html",
        "scripts/docs/**/*.py",
    ):
        assert f"{pattern} text eol=lf" in attributes


def test_authoritative_surface_map_has_one_outcome_per_surface() -> None:
    data = yaml.safe_load(
        (ROOT / "docs-content" / "metadata" / "ui_surfaces.yml").read_text(encoding="utf-8")
    )
    surfaces = data["surfaces"]
    screenshots = yaml.safe_load(
        (ROOT / "docs-content" / "metadata" / "screenshots.yml").read_text(encoding="utf-8")
    )["screenshots"]

    assert data["authority"] == "public-docs-surface-coverage"
    assert len(surfaces) >= 40
    for surface in surfaces.values():
        assert surface["status"] in {"ready", "missing"}
        assert surface["capture_type"] in {"automated", "manual"}
        assert bool(surface.get("screenshot_id")) != bool(surface.get("no_image_reason"))
        if surface.get("screenshot_id"):
            assert surface["screenshot_id"] in screenshots


def test_capture_refuses_screenshots_without_a_react_target(monkeypatch) -> None:
    import scripts.docs.capture_real_ui_screenshots as capture

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("capture prepared a profile or launched the app")

    monkeypatch.setattr(capture, "_safe_capture_data_dir", must_not_run)
    monkeypatch.setattr(capture, "_launch_app", must_not_run)
    retired = {
        "title": "Providers settings",
        "output": "settings-providers.png",
        "route": "/?settings_tab=Providers",
        "capture_selector": "main",
        "expected_text": ["Providers"],
    }
    react = {**retired, "route": "/app-v2/settings/providers"}

    assert capture._capture_target_problems(react) == []
    assert capture._capture_target_problems({"title": "No target"}) == []
    assert capture._capture_target_problems({"route": "/app-v2/?tab=monitor"}) == [
        "capture target is missing capture_selector",
        "capture target is missing expected_text",
    ]
    with pytest.raises(RuntimeError, match="React capture targets have not been written") as refused:
        capture.capture(
            {
                "settings-providers": retired,
                "home-monitor": {"title": "Monitor", "output": "home-monitor.png"},
                "settings-models": {**react, "route": "/app-v2/settings/models"},
            },
            scenario="full",
        )
    assert "home-monitor, settings-providers." in str(refused.value)
    assert "settings-models" not in str(refused.value)


def test_capture_rejects_the_real_user_data_directory(tmp_path: Path, monkeypatch) -> None:
    import scripts.docs.capture_real_ui_screenshots as capture

    real_dir = tmp_path / "real-profile"
    real_dir.mkdir()
    monkeypatch.setattr(capture, "_real_user_data_dir", lambda: real_dir.resolve())

    try:
        capture._safe_capture_data_dir(real_dir)
    except RuntimeError as exc:
        assert "normal Row-Bot data directory" in str(exc)
    else:  # pragma: no cover - explicit safety failure
        raise AssertionError("capture accepted the real Row-Bot data directory")


def test_capture_accepts_real_data_only_with_explicit_reviewed_opt_in(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import scripts.docs.capture_real_ui_screenshots as capture

    real_dir = tmp_path / "real-profile"
    other_dir = tmp_path / "other-profile"
    real_dir.mkdir()
    other_dir.mkdir()
    monkeypatch.setattr(capture, "_real_user_data_dir", lambda: real_dir.resolve())

    selected, temporary = capture._safe_capture_data_dir(
        real_dir,
        authorize_real_data=True,
    )

    assert selected == real_dir.resolve()
    assert temporary is None
    with pytest.raises(RuntimeError, match="exact normal Row-Bot data directory"):
        capture._safe_capture_data_dir(other_dir, authorize_real_data=True)


def test_authorized_real_capture_cannot_seed_or_switch_to_temporary_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import scripts.docs.capture_real_ui_screenshots as capture

    real_dir = tmp_path / "real-profile"
    real_dir.mkdir()
    monkeypatch.setattr(capture, "_real_user_data_dir", lambda: real_dir.resolve())

    with pytest.raises(RuntimeError, match="--no-seed-demo-data"):
        capture.capture(
            {},
            scenario="full",
            data_dir=real_dir,
            seed_demo_data=True,
            use_temp_data=False,
            authorize_real_data=True,
        )
    with pytest.raises(RuntimeError, match="retained data"):
        capture.capture(
            {},
            scenario="full",
            data_dir=real_dir,
            seed_demo_data=False,
            use_temp_data=True,
            authorize_real_data=True,
        )


def test_authorized_real_capture_does_not_offer_fake_provider_choices(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import scripts.docs.capture_real_ui_screenshots as capture

    launched: dict[str, object] = {}

    def fake_popen(*args, **kwargs):
        launched["args"] = args
        launched["kwargs"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr(capture, "LOG_ROOT", tmp_path / "logs")
    monkeypatch.setattr(capture.subprocess, "Popen", fake_popen)
    with ExitStack() as stack:
        capture._launch_app(43123, tmp_path / "profile", stack, real_data=True)

    env = launched["kwargs"]["env"]
    assert env["ROW_BOT_DOCS_REAL_DATA"] == "1"
    assert env["ROW_BOT_DOCS_FAKE_PROVIDERS"] == "0"



def test_demo_capture_runs_against_a_display_only_local_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import urllib.error
    import urllib.request

    import scripts.docs.capture_real_ui_screenshots as capture

    launched: dict[str, object] = {}

    def fake_popen(*_args, **kwargs):
        launched.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(capture, "LOG_ROOT", tmp_path / "logs")
    monkeypatch.setattr(capture.subprocess, "Popen", fake_popen)
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with ExitStack() as stack:
        capture._launch_app(43123, tmp_path / "profile", stack)
        host = launched["env"]["OLLAMA_HOST"]
        assert host.startswith("http://127.0.0.1:")
        with direct.open(f"{host}/api/tags", timeout=5) as response:
            listed = [model["name"] for model in json.loads(response.read())["models"]]
        assert listed == list(capture.DEMO_LOCAL_MODELS)
        chat = urllib.request.Request(
            f"{host}/api/chat",
            data=json.dumps({"model": listed[0], "messages": []}).encode(),
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as refused:
            direct.open(chat, timeout=5)
        assert refused.value.code == 503


def test_capture_seeds_first_run_screenshots_in_their_own_profile(monkeypatch) -> None:
    import scripts.docs.capture_real_ui_screenshots as capture

    profiles: dict[str, list[str]] = {}

    def fake_profile(shots, scenario, **_kwargs):
        profiles[scenario] = sorted(shots)
        return []

    monkeypatch.setattr(capture, "_capture_profile", fake_profile)
    monkeypatch.setattr(capture, "_write_report", lambda records, mode: {"records": records})
    target = {
        "title": "Demo",
        "output": "demo.png",
        "route": "/app-v2/",
        "capture_selector": "body",
        "expected_text": ["Row-Bot"],
    }

    capture.capture(
        {
            "first": {**target, "scenario": "first-run"},
            "home": {**target, "scenario": "configured"},
            "phone": {**target, "scenario": "mobile"},
        },
        scenario="full",
    )

    assert profiles == {"first-run": ["first"], "full": ["home", "phone"]}


def test_authorized_real_capture_uses_stable_anchors_not_demo_text() -> None:
    import scripts.docs.capture_real_ui_screenshots as capture

    selected = capture._real_data_shot({
        "route": "/app-v2/?tab=workflows",
        "capture_selector": '[data-home-tab="workflows"]',
        "expected_text": ["Morning Brief"],
        "actions": [
            {"click_selector": 'button[aria-label="Agent profiles"]'},
            {"wait_for_text": "Research Guide"},
        ],
    })

    assert selected["capture_selector"] == '[data-home-tab="workflows"]'
    assert selected["expected_text"] == []
    assert selected["actions"] == [{"click_selector": 'button[aria-label="Agent profiles"]'}]


def test_capture_publication_atomically_replaces_an_existing_asset(
    tmp_path: Path,
) -> None:
    import scripts.docs.capture_real_ui_screenshots as capture

    source = tmp_path / "rendered.png"
    destination = tmp_path / "public" / "surface.png"
    source.write_bytes(b"new image")
    destination.parent.mkdir()
    destination.write_bytes(b"old image")

    capture._publish_capture(source, destination)

    assert destination.read_bytes() == b"new image"
    assert list(destination.parent.glob(".*.tmp")) == []


def test_authorized_real_capture_is_review_only_not_a_public_asset() -> None:
    source = (
        ROOT
        / "scripts"
        / "docs"
        / "capture_real_ui_screenshots.py"
    ).read_text(encoding="utf-8")

    assert "if not real_data:" in source
    assert "_publish_capture(raw_output, output)" in source
    assert "raw_output if real_data else output" in source


def test_buddy_overlay_public_docs_cover_the_complete_user_workflow() -> None:
    buddy = (ROOT / "docs-site" / "docs" / "settings" / "buddy.mdx").read_text(
        encoding="utf-8"
    ).casefold()
    voice_and_buddy = (
        ROOT / "docs-site" / "docs" / "voice-and-buddy" / "index.mdx"
    ).read_text(encoding="utf-8").casefold()
    chat = (ROOT / "docs-site" / "docs" / "chat" / "index.mdx").read_text(
        encoding="utf-8"
    ).casefold()
    readme = (ROOT / "README.md").read_text(encoding="utf-8").casefold()
    combined = "\n".join((buddy, voice_and_buddy))

    for phrase in (
        "always-on-top",
        "open full thread",
        "dock buddy",
        "hide buddy",
        "enter sends",
        "shift+enter",
        "saved draft",
        "simple approvals",
        "complex approvals",
        "stop",
        "windows and macos",
        "browser/server mode",
        "talk and dictate",
    ):
        assert phrase in combined
    assert "/docs/settings/buddy" in chat
    assert "buddy desktop overlay" in readme
    for obsolete in (
        "enable switches decide whether buddy appears",
        "open and close overlay buttons",
        "toggle buddy visibility or reopen the overlay",
    ):
        assert obsolete not in combined



def test_react_settings_pages_and_home_tabs_have_docs_routes() -> None:
    from scripts.docs.collect_inventory import collect_home_tabs, collect_settings

    # Both collectors raise when the metadata and the React client disagree.
    pages = collect_settings()
    tabs = collect_home_tabs()

    assert {"providers", "tools", "access", "data"} <= {page["id"] for page in pages}
    assert {"overview", "workflows", "knowledge", "monitor"} <= {tab["id"] for tab in tabs}
    assert all(page["description"] and page["docs_route"].startswith("/docs/") for page in pages)
    assert all(tab["title"] and tab["docs_route"].startswith("/docs/") for tab in tabs)
    assert all((ROOT / tab["source"]).is_file() for tab in tabs)


def test_validator_rejects_fake_docs_screenshot_route(monkeypatch) -> None:
    import scripts.docs.validate_public_docs as validator

    original = validator._load_yaml

    def fake_load(path: Path):
        data = original(path)
        if path.name == "screenshots.yml":
            data = json.loads(json.dumps(data))
            first = next(iter(data["screenshots"].values()))
            first["route"] = "/docs-mode/surface/fake"
        return data

    monkeypatch.setattr(validator, "_load_yaml", fake_load)
    assert any("forbidden fake docs route" in error for error in validator.validate())


def test_public_docs_metadata_validates() -> None:
    assert validate() == []
