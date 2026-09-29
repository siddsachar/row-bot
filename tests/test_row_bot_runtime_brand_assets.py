from __future__ import annotations

from pathlib import Path

from PIL import Image

_RUNTIME_FILES = {"app.py", "launcher.py", "brand.py"}
_RUNTIME_PREFIXES = (
    "buddy/",
    "channels/",
    "designer/",
)
LEGACY_SERVICE_PREFIX = "Thoth"


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
    launcher_src = _read("src/row_bot/launcher.py")
    interaction_src = _read("src/row_bot/designer/interaction.py")
    telegram_src = _read("src/row_bot/channels/telegram.py")
    brand_src = _read("brand.py")

    assert (Path("static") / "favicon.ico").is_file()
    assert (Path("static") / "row_bot_glyph_256.png").is_file()
    assert Path("row-bot.ico").is_file()

    assert 'APP_BRAND_ACCENT = "#4F78A4"' in brand_src
    assert 'APP_BRAND_ACCENT_RGB = "79, 120, 164"' in brand_src

    assert "_APP_ICON_PATH = app_icon_path()" in launcher_src
    assert '_APP_GLYPH_PATH = static_dir() / "row_bot_glyph_256.png"' in launcher_src
    assert '_APP_FAVICON_PATH = static_dir() / "favicon.ico"' in launcher_src
    assert "_load_tray_base_icon" in launcher_src
    assert "_make_status_dot_icon" in launcher_src
    assert "_MacStatusItemBackend" in launcher_src
    assert "ROW_BOT_MAC_TRAY_BACKEND" in launcher_src
    assert "pyobjc-framework-cocoa" in Path("requirements.txt").read_text(encoding="utf-8").lower()
    assert "green = running, grey = stopped" not in launcher_src
    assert "tk.PhotoImage(file=GLYPH_PATH)" in launcher_src
    assert 'text="RB"' in launcher_src
    assert "\\U0001305F" not in launcher_src
    assert "SetCurrentProcessExplicitAppUserModelID" in launcher_src
    assert "self.Icon = Icon(_ICON_PATH)" in launcher_src
    assert "APP_BRAND_ACCENT" in launcher_src

    assert 'f"<b>{APP_DISPLAY_NAME}</b> is connected!' in telegram_src
    assert f"{LEGACY_SERVICE_PREFIX}</b> is connected" not in telegram_src
    assert "APP_BRAND_ACCENT" in interaction_src
    for legacy_blue in ("#3B82F6", "#3b82f6", "59, 130, 246", "96, 165, 250", "#f0c040"):
        assert legacy_blue not in interaction_src


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


def test_macos_tray_uses_status_dot_by_default(monkeypatch):
    import row_bot.launcher as launcher

    calls = {"count": 0}

    def _load_base_icon():
        calls["count"] += 1
        return Image.new("RGBA", (launcher._ICON_SIZE, launcher._ICON_SIZE), (10, 20, 30, 255))

    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.delenv("ROW_BOT_BRANDED_TRAY_ICON", raising=False)
    monkeypatch.setattr(launcher, "_icons", {}, raising=False)
    monkeypatch.setattr(launcher, "_load_tray_base_icon", _load_base_icon)

    running = launcher._get_icon("running")
    stopped = launcher._get_icon("stopped")

    assert calls["count"] == 0
    assert running.getpixel((launcher._ICON_SIZE // 2, launcher._ICON_SIZE // 2))[:3] == (34, 197, 94)
    assert stopped.getpixel((launcher._ICON_SIZE // 2, launcher._ICON_SIZE // 2))[:3] == (107, 114, 128)


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

    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "_icons", {}, raising=False)
    monkeypatch.setattr(launcher, "_load_tray_base_icon", _load_base_icon)

    first = launcher._get_icon("running")
    second = launcher._get_icon("running")

    assert first is second
    assert calls["count"] == 1
