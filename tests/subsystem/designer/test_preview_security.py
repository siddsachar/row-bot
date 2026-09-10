from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from row_bot.designer.html_ops import sanitize_agent_html
from row_bot.designer.interaction import inject_bridge_js, validate_bridge_event
from row_bot.designer.preview import isolate_preview_html, preview_fingerprint, render_multi_route_html
from row_bot.designer.runtime.loader import build_routes_payload
from row_bot.designer.state import BrandConfig, DesignerAsset, DesignerPage, DesignerProject

pytestmark = pytest.mark.subsystem


@pytest.mark.parametrize("payload", [
    '<script data-row-bot-runtime="1">parent.stolen = true</script>',
    '<SCRIPT data-row-bot-runtime="1">parent.stolen = true</SCRIPT>',
    '<svg onload="parent.stolen = true"></svg>',
    '<iframe srcdoc="<script>parent.stolen=true</script>"></iframe>',
    '<object data="data:text/html,bad"></object><embed src="/api/v1/">',
    '<meta http-equiv="refresh" content="0;url=/api/v1/"><base href="/">',
    '<a href="javascript:alert(1)" onclick="parent.stolen=true">hello</a>',
    '<a href="java&#x09;script:alert(1)">hello</a>',
])
def test_generated_content_cannot_claim_privilege(payload):
    result = BeautifulSoup(sanitize_agent_html(payload), "html.parser")
    assert not result.find_all(["script", "iframe", "object", "embed", "base"])
    assert not result.find_all(attrs={"data-row-bot-runtime": True})
    assert not result.find_all(attrs={"http-equiv": True})
    for tag in result.find_all(True):
        assert not any(attr.lower().startswith("on") for attr in tag.attrs)
        assert not any(isinstance(v, str) and v.lower().startswith("javascript:") for v in tag.attrs.values())


def test_trusted_runtime_injected_only_after_sanitization():
    project = DesignerProject(mode="app_mockup", pages=[DesignerPage(
        html='<html><body><script data-row-bot-runtime="1">FORGED_MARKER</script><h1>Safe</h1></body></html>',
        route_id="home", title='</script><script>ROUTE_INJECTION</script>',
    )])
    result = render_multi_route_html(project)
    soup = BeautifulSoup(result, "html.parser")
    assert "FORGED_MARKER" not in result
    assert len(soup.find_all("script")) == 2
    routes = json.loads(soup.find("script", id="__row_bot_routes__").string)
    assert routes["labels"]["home"] == project.pages[0].title
    assert "</script>" not in build_routes_payload(initial="home", order=["</script>"])


def test_preview_policy_denies_network_and_non_fragment_navigation():
    soup = BeautifulSoup(isolate_preview_html('<a href="/api/v1/" target="_top">Bad</a><a href="#section">Local</a>'), "html.parser")
    policy = soup.find("meta")["content"]
    assert "default-src 'none'" in policy
    assert "connect-src 'none'" in policy
    assert "script-src 'none'" in policy
    assert "href" not in soup.find_all("a")[0].attrs
    assert soup.find_all("a")[1]["href"] == "#section"


def test_preview_retains_embedded_assets_without_network_references():
    rendered = isolate_preview_html('''<style>@import url(https://example.invalid/font);
    .image {background:url('data:image/png;base64,AAAA')}
    .remote {background:url('/api/v1/secret')}</style>
    <img src="https://example.invalid/picture"><img src="data:image/png;base64,AAAA">''')
    assert "example.invalid" not in rendered
    assert "/api/v1/secret" not in rendered
    assert "url('data:image/png;base64,AAAA')" in rendered
    assert 'src="data:image/png;base64,AAAA"' in rendered


def test_render_module_has_no_nicegui_import(monkeypatch):
    import builtins
    import importlib
    from row_bot.designer import preview

    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "nicegui" or name.startswith("nicegui."):
            pytest.fail("Headless preview imported NiceGUI")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    importlib.reload(preview)
    project = DesignerProject(pages=[DesignerPage(html="<p>Headless</p>")])
    assert "Headless" in preview.render_page_html(project, project.pages[0].html)


def _event():
    return {"previewId": "frame-a", "revision": "rev-a", "capability": "token-a",
            "msgType": "text-edit", "detail": {"xpath": "/html/body/p[1]", "tag": "p",
                                                     "oldText": "Before", "newText": "After"}}


def test_current_validated_edit_preserves_existing_payload():
    assert validate_bridge_event(_event(), preview_id="frame-a", revision="rev-a", capability="token-a")
    html = inject_bridge_js("<body>Hello</body>", preview_id="frame-a", revision="rev-a", capability="token-a")
    assert '"previewId": "frame-a"' in html
    assert "sendToOwner(" in html
    assert inject_bridge_js("safe") == "safe"


@pytest.mark.parametrize("key,value", [
    ("previewId", "foreign"), ("revision", "old"), ("capability", ""),
    ("msgType", "native.execute"), ("detail", []), ("extra", "forged"),
])
def test_stale_or_forged_bridge_envelope_rejected(key, value):
    event = _event()
    event[key] = value
    assert not validate_bridge_event(event, preview_id="frame-a", revision="rev-a", capability="token-a")


def test_bridge_requires_bounded_typed_edit_detail():
    event = _event()
    event["detail"]["newText"] = "x" * 17000
    assert not validate_bridge_event(event, preview_id="frame-a", revision="rev-a", capability="token-a")
    event = _event()
    event["detail"]["newText"] = {"execute": True}
    assert not validate_bridge_event(event, preview_id="frame-a", revision="rev-a", capability="token-a")


def test_each_render_input_invalidates_before_html_construction(tmp_path, monkeypatch):
    from row_bot.designer import storage
    from row_bot.designer.history import UndoStack

    monkeypatch.setattr(storage, "ASSETS_DIR", tmp_path / "assets")
    project = DesignerProject(id="deck", brand=BrandConfig(), pages=[DesignerPage(html="<p>A</p>")])
    previous = preview_fingerprint(project)
    for mutate in (
        lambda: setattr(project.pages[0], "html", "<p>B</p>"),
        lambda: setattr(project.brand, "primary_color", "#ffffff"),
        lambda: project.pages.append(DesignerPage(html="<p>C</p>")),
        lambda: setattr(project, "active_page", 1),
        lambda: setattr(project, "canvas_width", 1440),
        lambda: setattr(project, "mode", "document"),
    ):
        mutate()
        current = preview_fingerprint(project)
        assert current != previous
        previous = current
    assert preview_fingerprint(project, preview_mode=True) != previous
    stack = UndoStack()
    stack.push(project)
    project.pages[0].html = "<p>Undo me</p>"
    current = preview_fingerprint(project)
    assert stack.undo(project)
    assert preview_fingerprint(project) != current
    assert stack.redo(project)
    assert preview_fingerprint(project) == current
    asset_dir = storage.ASSETS_DIR / project.id
    asset_dir.mkdir(parents=True)
    asset = asset_dir / "image.bin"
    asset.write_bytes(b"first")
    project.assets.append(DesignerAsset(id="image", stored_name="image.bin"))
    previous = preview_fingerprint(project)
    asset.write_bytes(b"replacement content")
    assert preview_fingerprint(project) != previous


def test_legacy_idle_ticks_skip_renderer_and_allow_undo_refresh(monkeypatch):
    import nicegui
    from row_bot.designer import preview

    class Element:
        id = 1
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def __getattr__(self, _):
            return lambda *a, **kw: self

    scripts = []
    fake = Element()
    fake.context = SimpleNamespace(client=SimpleNamespace(on_disconnect=lambda *_: None))
    fake.run_javascript = scripts.append
    monkeypatch.setattr(nicegui, "ui", fake)
    calls = []
    render = preview.render_page_html
    monkeypatch.setattr(preview, "render_page_html", lambda *a, **kw: (calls.append(1), render(*a, **kw))[1])
    project = DesignerProject(pages=[DesignerPage(html="<p>A</p>")])
    panel = preview.build_preview(project)
    assert len(calls) == 1
    for _ in range(120):
        panel["refresh"]()
    assert len(calls) == 1
    project.pages[0].html = "<p>B</p>"
    panel["refresh"]()
    assert len(calls) == 2
    panel["force_refresh"]()
    assert len(calls) == 3
