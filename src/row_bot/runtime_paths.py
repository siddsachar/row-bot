"""Runtime path helpers for source and packaged Row-Bot layouts."""

from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
APP_ROOT = SRC_DIR.parent


def app_root() -> Path:
    """Return the repository root or installed app payload root."""

    return APP_ROOT


def app_path(*parts: str) -> Path:
    return APP_ROOT.joinpath(*parts)


def static_dir() -> Path:
    return app_path("static")


def sounds_dir() -> Path:
    return app_path("sounds")


def bundled_skills_dir() -> Path:
    return app_path("bundled_skills")


def tool_guides_dir() -> Path:
    return app_path("tool_guides")


def app_icon_path() -> Path:
    return app_path("row-bot.ico")
