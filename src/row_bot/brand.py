"""Canonical Row-Bot product identity constants."""

from __future__ import annotations

from pathlib import Path

from row_bot.version import __version__

APP_DISPLAY_NAME = "Row-Bot"
APP_SLUG = "row-bot"
APP_PYTHON_IDENTIFIER = "row_bot"
APP_COMPACT_NAME = "RowBot"
APP_PING_ID = APP_SLUG
APP_BRAND_ACCENT = "#4F78A4"
APP_BRAND_ACCENT_RGB = "79, 120, 164"

APP_WEBSITE_URL = "https://row-bot.ai"
APP_REPOSITORY = "siddsachar/row-bot"
APP_REPOSITORY_URL = f"https://github.com/{APP_REPOSITORY}"
APP_RELEASES_URL = f"https://api.github.com/repos/{APP_REPOSITORY}/releases"
APP_RELEASES_LATEST_URL = f"{APP_RELEASES_URL}/latest"
APP_SUPPORT_URL = f"{APP_WEBSITE_URL}/contact.html"
APP_SECURITY_URL = f"{APP_REPOSITORY_URL}/security"

APP_USER_AGENT = f"{APP_DISPLAY_NAME}/{__version__}"
UPDATER_USER_AGENT = f"{APP_DISPLAY_NAME}-Updater/{__version__}"
DESIGNER_USER_AGENT = f"{APP_DISPLAY_NAME}-Designer/1.0"
GITHUB_USER_AGENT = f"{APP_DISPLAY_NAME}-GitHub/1.0"

APP_DATA_DIR_ENV = "ROW_BOT_DATA_DIR"
APP_PORT_ENV = "ROW_BOT_PORT"
APP_HOST_ENV = "ROW_BOT_HOST"
APP_STARTUP_TIMEOUT_ENV = "ROW_BOT_STARTUP_TIMEOUT"
APP_AUTO_START_OLLAMA_ENV = "ROW_BOT_AUTO_START_OLLAMA"
APP_NATIVE_ENV = "ROW_BOT_NATIVE"
APP_WEBVIEW_STORAGE_ENV = "ROW_BOT_WEBVIEW_STORAGE_PATH"
APP_WORKSPACE_ENV = "ROW_BOT_WORKSPACE"
APP_TEST_MODE_ENV = "ROW_BOT_TEST_MODE"

DEFAULT_DATA_DIR_NAME = ".row-bot"
DEFAULT_WORKSPACE_DIR_NAME = "Row-Bot"
LINUX_APP_HOME_NAME = APP_SLUG
LINUX_COMMAND_NAME = APP_SLUG

WINDOWS_INSTALLER_BASENAME = APP_DISPLAY_NAME
LEGACY_WINDOWS_INSTALLER_BASENAME = "RowBotSetup"
MACOS_APP_BUNDLE_NAME = "Row-Bot.app"
MACOS_BUNDLE_ID = "ai.row-bot.assistant"
LINUX_DESKTOP_ID = "ai.row-bot.RowBot.desktop"
UPDATE_MANIFEST_MARKER = "row-bot-update-manifest"

KEYRING_SERVICE_PREFIX = APP_DISPLAY_NAME
LAUNCHER_APP_LOG_FILENAME = "row_bot_app.log"
WINDOW_LOG_FILENAME = "row_bot_window.log"
STRUCTURED_LOG_FILENAME = "row_bot.log"


def default_data_dir() -> Path:
    """Return the canonical Row-Bot data directory path without creating it."""
    return Path.home() / DEFAULT_DATA_DIR_NAME
