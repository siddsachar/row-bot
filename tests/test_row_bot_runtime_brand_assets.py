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


def test_row_bot_glyph_assets_have_no_transparent_padding():
    for path in (
        Path("static") / "row_bot_glyph_256.png",
        Path("docs") / "row_bot_glyph_256.png",
        Path("docs") / "row_bot_glyph.png",
    ):
        image = Image.open(path).convert("RGBA")
        assert image.getchannel("A").getbbox() == (0, 0, image.width, image.height)

    for path in (Path("row-bot.ico"), Path("static") / "favicon.ico"):
        image = Image.open(path).convert("RGBA")
        left, _top, right, _bottom = _alpha_bbox(path)
        assert left == 0
        assert right == image.width
