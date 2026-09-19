from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from bs4 import BeautifulSoup

from row_bot.designer import client_service as service
from row_bot.designer import preview, storage
from row_bot.designer.state import ASPECT_RATIOS, DESIGNER_MODES, DesignerPage

pytestmark = pytest.mark.subsystem
MODES = tuple(DESIGNER_MODES)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "designer"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    for key, path in (("DESIGNER_DIR", root), ("PROJECTS_DIR", root / "projects"),
                      ("ASSETS_DIR", root / "assets"), ("REFERENCES_DIR", root / "references")):
        monkeypatch.setattr(storage, key, path)
    # Creation and read-only previews must neither select a Studio session nor
    # reach a provider/font URL even when a nonempty first-draft brief is saved.
    import row_bot.designer.session as session
    import socket
    monkeypatch.setattr(session, "set_active_project", lambda *_: pytest.fail("session changed"))
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network call"))
    return root


@pytest.mark.parametrize("mode", MODES)
def test_each_mode_has_real_defaults_and_retry_identity(isolated, mode):
    options = service.artifact_setup_options(mode)
    assert options.mode == mode
    assert options.default_template in {t.id for t in options.templates}
    assert options.default_canvas in {c.id for c in options.canvases}
    setup = service.ArtifactSetup(mode=mode, brief="A synthetic first draft brief")
    with ThreadPoolExecutor(max_workers=4) as executor:
        projects = list(executor.map(lambda _: service.create_artifact("artifact", setup), range(8)))
    assert len({p.updated_at for p in projects}) == 1
    project = projects[0]
    assert project.id == "artifact"
    assert project.mode == mode
    assert project.name == options.default_name
    assert project.template_id == options.default_template
    assert project.aspect_ratio == options.default_canvas
    assert (project.canvas_width, project.canvas_height) == ASPECT_RATIOS[options.default_canvas]
    assert project.pages and all(p.kind == DESIGNER_MODES[mode]["page_kind"] for p in project.pages)
    assert project.brief.build_description == setup.brief
    assert project.thread_id is None and project.thread_ownership == "resume"
    assert len(list(storage.PROJECTS_DIR.glob("*.json"))) == 1


@pytest.mark.parametrize("mode", MODES)
def test_every_advertised_template_and_canvas_can_be_created(isolated, mode):
    options = service.artifact_setup_options(mode)
    for template in options.templates:
        for canvas in options.canvases:
            project = service.create_artifact(f"artifact-{template.id}-{canvas.id.replace(':', '-')}",
                service.ArtifactSetup(mode=mode, template_id=template.id, aspect_ratio=canvas.id))
            assert project.mode == mode and project.template_id == template.id
            assert project.pages and project.aspect_ratio == canvas.id
            rendered = service.read_preview(project.id)
            assert rendered.mode == mode and rendered.page_count == len(project.pages)
            assert (rendered.canvas_width, rendered.canvas_height) == ASPECT_RATIOS[canvas.id]
            assert rendered.html and not rendered.unchanged


@pytest.mark.parametrize("setup,code", [
    (service.ArtifactSetup(mode="app"), "artifact_type_unavailable"),
    (service.ArtifactSetup(mode="auto"), "artifact_type_unavailable"),
    (service.ArtifactSetup(mode="unknown"), "artifact_type_unavailable"),
    (service.ArtifactSetup(mode="document", template_id="blank_deck"), "invalid_template"),
    (service.ArtifactSetup(mode="deck", template_id="blank_canvas"), "invalid_template"),
    (service.ArtifactSetup(mode="landing", aspect_ratio="16:9"), "invalid_canvas"),
    (service.ArtifactSetup(mode="app_mockup", aspect_ratio="A4"), "invalid_canvas"),
    (service.ArtifactSetup(mode="storyboard", brief="x" * 20001), "invalid_setup"),
    (service.ArtifactSetup(mode="document", name="x" * 201), "invalid_setup"),
    (service.ArtifactSetup(mode="document", template_id=None), "invalid_setup"),
])
def test_invalid_setup_does_not_write_or_coerce_type(isolated, setup, code):
    with pytest.raises(service.ArtifactError, match=code):
        service.create_artifact("artifact", setup)
    assert not isolated.exists()


def test_existing_identity_cannot_be_reinterpreted_as_another_mode(isolated):
    service.create_artifact("artifact", service.ArtifactSetup(mode="document"))
    before = (storage.PROJECTS_DIR / "artifact.json").read_bytes()
    with pytest.raises(service.ArtifactError, match="resource_state_invalid"):
        service.create_artifact("artifact", service.ArtifactSetup(mode="deck"))
    assert (storage.PROJECTS_DIR / "artifact.json").read_bytes() == before


def test_library_enables_real_modes_keeps_unknown_and_preserves_legacy_data(isolated):
    for mode in MODES:
        service.create_artifact(mode, service.ArtifactSetup(mode=mode))
    raw = json.loads((storage.PROJECTS_DIR / "deck.json").read_text(encoding="utf-8"))
    raw.update(id="unsupported", mode="future_type")
    unknown_path = storage.PROJECTS_DIR / "unsupported.json"
    unknown_path.write_text(json.dumps(raw), encoding="utf-8")
    raw.pop("mode")
    raw["id"] = "legacy"
    (storage.PROJECTS_DIR / "legacy.json").write_text(json.dumps(raw), encoding="utf-8")
    before = unknown_path.read_bytes()
    items = {item.id: item for item in service.list_artifacts().items}
    assert all(items[mode].available and items[mode].mode == mode for mode in MODES)
    assert items["legacy"].available and items["legacy"].mode == "deck"
    assert not items["unsupported"].available and items["unsupported"].mode == "unknown"
    with pytest.raises(service.ArtifactError, match="artifact_type_unavailable"):
        service.read_preview("unsupported")
    with pytest.raises(service.ArtifactError, match="artifact_type_unavailable"):
        service.associate_origin("unsupported", "ordinary-chat", expected_revision=raw["updated_at"],
                                 expected_origin=None)
    assert unknown_path.read_bytes() == before


@pytest.mark.parametrize("mode", MODES)
def test_preview_is_real_revisioned_and_unchanged_reads_do_no_render(isolated, monkeypatch, mode):
    project = service.create_artifact("artifact", service.ArtifactSetup(mode=mode))
    initial_count = len(project.pages)
    project.pages.append(DesignerPage(title="Second", route_id="second", html="<html><body>Second</body></html>"))
    storage.save_project(project)
    first = service.read_preview(project.id, page_id="second")
    assert first.mode == mode and first.page_id == "second" and first.page_index == initial_count
    assert first.page_count == initial_count + 1 and "Second" in first.html
    interactive = mode in preview.INTERACTIVE_MODES
    assert first.scripts_allowed is interactive
    if interactive:
        soup = BeautifulSoup(first.html, "html.parser")
        routes = json.loads(soup.find("script", id="__row_bot_routes__").string)
        assert routes["initial"] == "second" and len(routes["order"]) == initial_count + 1
        assert len(soup.select("section[data-row-bot-route-host]")) == initial_count + 1
    with monkeypatch.context() as guards:
        for name in ("render_page_html", "render_multi_route_html", "isolate_preview_html"):
            guards.setattr(preview, name, lambda *a, **kw: pytest.fail("unchanged render work"))
        for _ in range(120):
            unchanged = service.read_preview(project.id, page_id="second", known_revision=first.preview_revision)
            assert unchanged.unchanged and unchanged.html is None
            assert unchanged.scripts_allowed is interactive
    project.brand.primary_color = "#aabbcc"
    storage.save_project(project)
    changed = service.read_preview(project.id, page_id="second", known_revision=first.preview_revision)
    assert not changed.unchanged and "#aabbcc" in changed.html
    assert changed.preview_revision != first.preview_revision


@pytest.mark.parametrize("mode", MODES)
def test_preview_removes_generated_authority_before_trusted_runtime(isolated, mode):
    project = service.create_artifact("artifact", service.ArtifactSetup(mode=mode))
    project.pages[0].html = '''<html><body>
    <script data-row-bot-runtime="1">FORGED_EXECUTION</script>
    <iframe srcdoc="<script>FORGED_EXECUTION</script>"></iframe>
    <img src="/api/v1/secrets" onerror="FORGED_EXECUTION">
    <a href="javascript:FORGED_EXECUTION" target="_top">Unsafe</a>
    <a href="https://example.invalid">Remote</a>
    </body></html>'''
    storage.save_project(project)
    result = service.read_preview(project.id)
    assert "FORGED_EXECUTION" not in result.html
    assert "/api/v1/secrets" not in result.html and "example.invalid" not in result.html
    soup = BeautifulSoup(result.html, "html.parser")
    assert not soup.find_all(["iframe", "object", "embed", "base"])
    assert not any(key.startswith("on") for tag in soup.find_all(True) for key in tag.attrs)
    policy = soup.find("meta", attrs={"http-equiv": "Content-Security-Policy"})["content"]
    assert "connect-src 'none'" in policy and "frame-src 'none'" in policy
    assert "form-action 'none'" in policy and "default-src 'none'" in policy
    if mode in preview.INTERACTIVE_MODES:
        assert result.scripts_allowed and len(soup.find_all("script")) == 2
        assert "script-src 'unsafe-inline'" in policy
    else:
        assert not result.scripts_allowed and not soup.find_all("script")
        assert "script-src 'none'" in policy


@pytest.mark.parametrize("mode", MODES)
def test_origin_reuse_is_independent_of_cleanup_ownership(isolated, mode):
    created = service.create_artifact("artifact", service.ArtifactSetup(mode=mode))
    associated = service.associate_origin(created.id, "ordinary-chat", expected_revision=created.updated_at,
                                         expected_origin=None)
    assert associated.thread_id == "ordinary-chat" and associated.thread_ownership == "resume"
    assert storage.detach_thread(created.id, "ordinary-chat")
    orphan = service.read_artifact(created.id)
    assert orphan.missing_origin_thread_id == "ordinary-chat"
    assert service.read_preview(created.id).mode == mode
    with pytest.raises(service.ArtifactError, match="origin_repair_required"):
        service.associate_origin(created.id, "another-chat", expected_revision=orphan.updated_at,
                                 expected_origin="ordinary-chat")
    repaired = service.associate_origin(created.id, "another-chat", expected_revision=orphan.updated_at,
                                       expected_origin="ordinary-chat", repair=True)
    assert repaired.thread_id == "another-chat" and repaired.thread_ownership == "resume"


@pytest.mark.parametrize("mode", MODES)
def test_late_render_does_not_publish_stale_mode_preview(isolated, monkeypatch, mode):
    project = service.create_artifact("artifact", service.ArtifactSetup(mode=mode))
    original = preview.isolate_preview_html

    def save_during_render(*args, **kwargs):
        markup = original(*args, **kwargs)
        project.pages[0].html = "<p>Newer saved content</p>"
        storage.save_project(project)
        return markup

    monkeypatch.setattr(preview, "isolate_preview_html", save_during_render)
    with pytest.raises(service.ArtifactError, match="resource_revision_conflict"):
        service.read_preview(project.id)
    assert "Newer saved content" in service.read_artifact(project.id).pages[0].html


@pytest.mark.parametrize("mode", ["landing", "app_mockup", "storyboard"])
def test_legacy_duplicate_and_whitespace_routes_select_exact_rendered_page(isolated, mode):
    project = service.create_artifact("artifact", service.ArtifactSetup(mode=mode))
    project.pages = [
        DesignerPage(title="First", route_id="  duplicate  ", html="<p>First page</p>"),
        DesignerPage(title="Second", route_id="duplicate", html="<p>Second page</p>"),
        DesignerPage(title="Third", route_id="   ", html="<p>Third page</p>"),
    ]
    project.active_page = 1
    storage.save_project(project)
    saved = (storage.PROJECTS_DIR / "artifact.json").read_bytes()
    initial = service.read_preview(project.id)
    assert initial.page_id == "duplicate-2" and initial.page_index == 1
    assert [p.id for p in initial.pages] == ["duplicate", "duplicate-2", "third"]
    for page in initial.pages:
        selected = service.read_preview(project.id, page_id=page.id)
        assert selected.page_id == page.id and selected.page_index == page.index
        soup = BeautifulSoup(selected.html, "html.parser")
        routes = json.loads(soup.find("script", id="__row_bot_routes__").string)
        assert routes["initial"] == page.id
        hosts = soup.select("section[data-row-bot-route-host]")
        assert hosts[page.index]["data-row-bot-route"] == page.id
        assert page.title in hosts[page.index].get_text()
    assert (storage.PROJECTS_DIR / "artifact.json").read_bytes() == saved
