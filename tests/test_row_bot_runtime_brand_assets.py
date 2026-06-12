from __future__ import annotations

from pathlib import Path

from PIL import Image

from row_bot.migration.row_bot_legacy_rebrand import LEGACY_SERVICE_PREFIX

_RUNTIME_FILES = {"app.py", "launcher.py", "brand.py"}
_RUNTIME_PREFIXES = (
    "buddy/",
    "channels/",
    "designer/",
    "ui/",
)


def _read(path: str) -> str:
    if path in _RUNTIME_FILES or path.startswith(_RUNTIME_PREFIXES):
        path = f"src/row_bot/{path}"
    return Path(path).read_text(encoding="utf-8")


def _alpha_bbox(path: Path) -> tuple[int, int, int, int]:
    image = Image.open(path).convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    assert bbox is not None
    return bbox


def test_runtime_brand_assets_are_file_backed_and_visible():
    app_src = _read("src/row_bot/app.py")
    sidebar_src = _read("src/row_bot/ui/sidebar.py")
    buddy_src = _read("src/row_bot/ui/buddy.py")
    launcher_src = _read("src/row_bot/launcher.py")
    status_src = _read("src/row_bot/ui/status_bar.py")
    helpers_src = _read("src/row_bot/ui/helpers.py")
    head_src = _read("src/row_bot/ui/head_html.py")
    command_src = _read("src/row_bot/ui/command_center.py")
    task_dialog_src = _read("src/row_bot/ui/task_dialog.py")
    designer_ui_src = _read("src/row_bot/designer/ui_theme.py")
    template_gallery_src = _read("src/row_bot/designer/template_gallery.py")
    page_navigator_src = _read("src/row_bot/designer/page_navigator.py")
    interaction_src = _read("src/row_bot/designer/interaction.py")
    telegram_src = _read("src/row_bot/channels/telegram.py")
    brand_src = _read("brand.py")

    assert (Path("static") / "favicon.ico").is_file()
    assert (Path("static") / "row_bot_glyph_256.png").is_file()
    assert Path("row-bot.ico").is_file()

    assert 'APP_BRAND_ACCENT = "#4F78A4"' in brand_src
    assert 'APP_BRAND_ACCENT_RGB = "79, 120, 164"' in brand_src

    assert '"favicon": Path(_static_dir) / "favicon.ico"' in app_src
    assert 'ui.image("/static/row_bot_glyph_256.png")' in app_src
    assert "width: 144px; height: 144px" in app_src
    assert "APP_BRAND_ACCENT" in app_src

    assert '<img src="/static/row_bot_glyph_256.png"' in sidebar_src
    assert "width:72px; height:auto" in sidebar_src
    assert "flex-direction:column; gap:3px" in sidebar_src
    assert "Personal AI Sovereignty</span></div></div>" in sidebar_src
    assert 'ui.label("Personal AI Sovereignty")' not in sidebar_src
    assert "APP_BRAND_ACCENT" in sidebar_src

    assert "_APP_ICON_PATH = app_icon_path()" in launcher_src
    assert '_APP_GLYPH_PATH = static_dir() / "row_bot_glyph_256.png"' in launcher_src
    assert '_APP_FAVICON_PATH = static_dir() / "favicon.ico"' in launcher_src
    assert "_load_tray_base_icon" in launcher_src
    assert "_make_macos_template_tray_icon" in launcher_src
    assert "_MacStatusItemTrayIcon" in launcher_src
    assert "ROW_BOT_MAC_TRAY_BACKEND" in launcher_src
    assert "_make_status_dot_icon" in launcher_src
    assert "green = running, grey = stopped" not in launcher_src
    assert "tk.PhotoImage(file=GLYPH_PATH)" in launcher_src
    assert 'text="RB"' in launcher_src
    assert "\\U0001305F" not in launcher_src
    assert "SetCurrentProcessExplicitAppUserModelID" in launcher_src
    assert "self.Icon = Icon(_ICON_PATH)" in launcher_src
    assert "APP_BRAND_ACCENT" in launcher_src

    assert "_DEFAULT_COLOR = APP_BRAND_ACCENT" in status_src
    assert "\U0001305F" not in status_src
    assert 'role = "User" if msg["role"] == "user" else APP_DISPLAY_NAME' in helpers_src
    assert 'f"<b>{APP_DISPLAY_NAME}</b> is connected!' in telegram_src
    assert f"{LEGACY_SERVICE_PREFIX}</b> is connected" not in telegram_src

    assert "__ROW_BOT_BRAND_ACCENT__" in head_src
    assert "Bot name = brand accent" in head_src
    assert "APP_BRAND_ACCENT" in command_src
    assert "APP_BRAND_ACCENT" in task_dialog_src
    assert "APP_BRAND_ACCENT" in designer_ui_src
    assert "APP_BRAND_ACCENT" in template_gallery_src
    assert "APP_BRAND_ACCENT" in page_navigator_src
    assert "APP_BRAND_ACCENT" in interaction_src

    assert "row-bot-buddy-v6" in buddy_src
    assert "window.RowBotBuddy" in buddy_src
    assert f"window.{LEGACY_SERVICE_PREFIX}Buddy" not in buddy_src

    for src in (app_src, sidebar_src, head_src, command_src):
        assert "color: gold" not in src
        assert "gold !important" not in src
        assert "#FFD700" not in src
        assert "#ffd54f" not in src

    for src in (
        app_src,
        sidebar_src,
        head_src,
        task_dialog_src,
        designer_ui_src,
        template_gallery_src,
        page_navigator_src,
        interaction_src,
    ):
        assert "#3B82F6" not in src
        assert "#3b82f6" not in src
        assert "59, 130, 246" not in src
        assert "96, 165, 250" not in src
        assert "#f0c040" not in src


def test_row_bot_glyph_assets_are_visible_and_normalized_for_tray():
    import row_bot.launcher as launcher

    for path in (
        Path("static") / "row_bot_glyph_256.png",
        Path("docs") / "row_bot_glyph_256.png",
        Path("docs") / "row_bot_glyph.png",
    ):
        image = Image.open(path).convert("RGBA")
        assert image.getchannel("A").getbbox() is not None
        normalized = launcher._normalize_tray_icon(image)
        assert normalized.mode == "RGBA"
        assert normalized.size == (launcher._ICON_SIZE, launcher._ICON_SIZE)
        assert normalized.getchannel("A").getbbox() is not None

    for path in (Path("row-bot.ico"), Path("static") / "favicon.ico"):
        image = Image.open(path).convert("RGBA")
        left, _top, right, _bottom = _alpha_bbox(path)
        assert left == 0
        assert right == image.width


def test_tray_icon_uses_branded_asset_when_available(tmp_path, monkeypatch):
    import row_bot.launcher as launcher

    glyph = tmp_path / "glyph.png"
    Image.new("RGBA", (48, 48), (10, 20, 30, 255)).save(glyph)
    missing = tmp_path / "missing.ico"

    monkeypatch.setattr(launcher, "_APP_GLYPH_PATH", glyph)
    monkeypatch.setattr(launcher, "_APP_ICON_PATH", missing)
    monkeypatch.setattr(launcher, "_APP_FAVICON_PATH", missing)
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "_icons", {}, raising=False)
    monkeypatch.setattr(launcher, "_tray_base_icon_loaded", False, raising=False)
    monkeypatch.setattr(launcher, "_tray_base_icon", None, raising=False)

    icon = launcher._get_icon("running")

    assert icon.mode == "RGBA"
    assert icon.size == (launcher._ICON_SIZE, launcher._ICON_SIZE)
    assert icon.getpixel((launcher._ICON_SIZE // 2, launcher._ICON_SIZE // 2))[:3] == (10, 20, 30)
    assert len(icon.getcolors(maxcolors=4096) or []) > 1


def test_macos_template_tray_icon_uses_glyph_alpha_mask(monkeypatch):
    import row_bot.launcher as launcher

    calls = {"count": 0}

    def _load_base_icon():
        calls["count"] += 1
        image = Image.new("RGBA", (launcher._ICON_SIZE, launcher._ICON_SIZE), (10, 20, 30, 0))
        for x in range(20, 44):
            for y in range(20, 44):
                image.putpixel((x, y), (10, 20, 30, 255))
        return image

    monkeypatch.setattr(launcher, "_load_tray_base_icon", _load_base_icon)

    icon = launcher._make_macos_template_tray_icon()

    assert calls["count"] == 1
    assert icon.mode == "RGBA"
    assert icon.size == (launcher._ICON_SIZE, launcher._ICON_SIZE)
    assert icon.getpixel((launcher._ICON_SIZE // 2, launcher._ICON_SIZE // 2)) == (0, 0, 0, 255)
    assert icon.getpixel((0, 0))[3] == 0


def test_macos_pystray_fallback_still_uses_status_dot_by_default(monkeypatch):
    import row_bot.launcher as launcher

    calls = {"count": 0}

    def _load_base_icon():
        calls["count"] += 1
        return Image.new("RGBA", (launcher._ICON_SIZE, launcher._ICON_SIZE), (10, 20, 30, 255))

    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.delenv("ROW_BOT_BRANDED_TRAY_ICON", raising=False)
    monkeypatch.setattr(launcher, "_icons", {}, raising=False)
    monkeypatch.setattr(launcher, "_load_tray_base_icon", _load_base_icon)

    icon = launcher._get_icon("running")

    assert calls["count"] == 0
    assert icon.mode == "RGBA"
    assert icon.size == (launcher._ICON_SIZE, launcher._ICON_SIZE)
    assert icon.getpixel((launcher._ICON_SIZE // 2, launcher._ICON_SIZE // 2))[:3] == (34, 197, 94)


def test_tray_icon_falls_back_safely_when_assets_are_missing(tmp_path, monkeypatch):
    import row_bot.launcher as launcher

    missing = tmp_path / "missing.png"
    monkeypatch.setattr(launcher, "_APP_GLYPH_PATH", missing)
    monkeypatch.setattr(launcher, "_APP_ICON_PATH", missing)
    monkeypatch.setattr(launcher, "_APP_FAVICON_PATH", missing)
    monkeypatch.setattr(launcher, "_icons", {}, raising=False)
    monkeypatch.setattr(launcher, "_tray_base_icon_loaded", False, raising=False)
    monkeypatch.setattr(launcher, "_tray_base_icon", None, raising=False)

    running = launcher._get_icon("running")
    stopped = launcher._get_icon("stopped")

    assert running.mode == "RGBA"
    assert stopped.mode == "RGBA"
    assert running.size == (launcher._ICON_SIZE, launcher._ICON_SIZE)
    assert stopped.size == (launcher._ICON_SIZE, launcher._ICON_SIZE)
    assert running.tobytes() != stopped.tobytes()


def test_tray_icon_caches_by_state(monkeypatch):
    import row_bot.launcher as launcher

    calls = {"count": 0}

    def _load_base_icon():
        calls["count"] += 1
        return Image.new("RGBA", (launcher._ICON_SIZE, launcher._ICON_SIZE), (10, 20, 30, 255))

    monkeypatch.setattr(launcher, "_icons", {}, raising=False)
    monkeypatch.setattr(launcher, "_load_tray_base_icon", _load_base_icon)

    first = launcher._get_icon("running")
    second = launcher._get_icon("running")

    assert first is second
    assert calls["count"] == 1


def test_pystray_backend_constructs_legacy_tray_icon(monkeypatch):
    import sys
    import types

    import row_bot.launcher as launcher
    from row_bot.brand import APP_DISPLAY_NAME

    callbacks = {
        "open": lambda: None,
        "open_browser": lambda: None,
        "show_buddy": lambda: None,
        "hide_buddy": lambda: None,
        "quit": lambda: None,
    }
    created = {}

    class FakeMenu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    class FakeMenuItem:
        def __init__(self, text, action, default=False):
            self.text = text
            self.action = action
            self.default = default

    class FakeIcon:
        def __init__(self, *, name, icon, title, menu):
            created.update(name=name, icon=icon, title=title, menu=menu)

        def run(self):
            pass

        def stop(self):
            pass

    fake_pystray = types.SimpleNamespace(Menu=FakeMenu, MenuItem=FakeMenuItem, Icon=FakeIcon)
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    monkeypatch.setattr(launcher, "_icons", {}, raising=False)

    tray_icon = launcher._PystrayTrayIcon(callbacks=callbacks)

    assert isinstance(tray_icon._icon, FakeIcon)
    assert created["name"] == APP_DISPLAY_NAME
    assert created["title"] == f"{APP_DISPLAY_NAME} - stopped"
    assert created["menu"].items[0].default is True
    assert created["menu"].items[0].action is callbacks["open"]


def test_native_macos_tray_backend_selected_by_default(monkeypatch):
    import row_bot.launcher as launcher

    callbacks = {
        "open": lambda: None,
        "open_browser": lambda: None,
        "show_buddy": lambda: None,
        "hide_buddy": lambda: None,
        "quit": lambda: None,
    }

    class FakeNative:
        backend_name = "macos_native"

        def __init__(self, *, callbacks):
            self.callbacks = callbacks

    class FakePystray:
        backend_name = "pystray"

        def __init__(self, *, callbacks):
            self.callbacks = callbacks

    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.delenv("ROW_BOT_MAC_TRAY_BACKEND", raising=False)
    monkeypatch.setattr(launcher, "_MacStatusItemTrayIcon", FakeNative)
    monkeypatch.setattr(launcher, "_PystrayTrayIcon", FakePystray)

    icon = launcher._create_tray_icon(callbacks=callbacks)

    assert isinstance(icon, FakeNative)
    assert icon.callbacks is callbacks


def test_macos_tray_backend_can_use_pystray_escape_hatch(monkeypatch):
    import row_bot.launcher as launcher

    callbacks = {
        "open": lambda: None,
        "open_browser": lambda: None,
        "show_buddy": lambda: None,
        "hide_buddy": lambda: None,
        "quit": lambda: None,
    }

    class FakeNative:
        backend_name = "macos_native"

        def __init__(self, *, callbacks):
            raise AssertionError("native backend should not be constructed")

    class FakePystray:
        backend_name = "pystray"

        def __init__(self, *, callbacks):
            self.callbacks = callbacks

    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setenv("ROW_BOT_MAC_TRAY_BACKEND", "pystray")
    monkeypatch.setattr(launcher, "_MacStatusItemTrayIcon", FakeNative)
    monkeypatch.setattr(launcher, "_PystrayTrayIcon", FakePystray)

    icon = launcher._create_tray_icon(callbacks=callbacks)

    assert isinstance(icon, FakePystray)


def test_macos_tray_backend_falls_back_to_pystray_on_native_error(monkeypatch):
    import row_bot.launcher as launcher

    callbacks = {
        "open": lambda: None,
        "open_browser": lambda: None,
        "show_buddy": lambda: None,
        "hide_buddy": lambda: None,
        "quit": lambda: None,
    }

    class BrokenNative:
        backend_name = "macos_native"

        def __init__(self, *, callbacks):
            raise RuntimeError("no status item")

    class FakePystray:
        backend_name = "pystray"

        def __init__(self, *, callbacks):
            self.callbacks = callbacks

    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.delenv("ROW_BOT_MAC_TRAY_BACKEND", raising=False)
    monkeypatch.setattr(launcher, "_MacStatusItemTrayIcon", BrokenNative)
    monkeypatch.setattr(launcher, "_PystrayTrayIcon", FakePystray)

    icon = launcher._create_tray_icon(callbacks=callbacks)

    assert isinstance(icon, FakePystray)
