from __future__ import annotations

import os
from pathlib import Path

import pytest

from row_bot import client_assets
from row_bot.status_checks import check_client_build

BUILT = 1_790_000_000


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    frontend = tmp_path / "frontend"
    for name in ("package.json", "index.html", "dist/.vite/manifest.json", "src/App.tsx", "src/App.test.tsx"):
        (frontend / name).parent.mkdir(parents=True, exist_ok=True)
        (frontend / name).write_text("{}")
        os.utime(frontend / name, (BUILT - 60, BUILT - 60))
    os.utime(frontend / "dist/.vite/manifest.json", (BUILT, BUILT))
    monkeypatch.setattr(client_assets, "_development_checkout", lambda: tmp_path)
    return tmp_path


def touch(path: Path, when: int) -> None:
    os.utime(path, (when, when))


def test_a_checkout_says_its_client_build_is_current(checkout: Path) -> None:
    (result,) = check_client_build()

    assert (result.name, result.status) == ("Client build", "ok")


def test_a_source_change_after_the_build_warns_until_the_next_build(checkout: Path) -> None:
    touch(checkout / "frontend/src/App.tsx", BUILT + 3600)

    (result,) = check_client_build()
    assert result.status == "warn"
    assert "npm --prefix frontend run build" in result.detail

    touch(checkout / "frontend/dist/.vite/manifest.json", BUILT + 7200)
    (result,) = check_client_build()
    assert result.status == "ok"


def test_a_changed_test_file_does_not_make_the_build_stale(checkout: Path) -> None:
    touch(checkout / "frontend/src/App.test.tsx", BUILT + 3600)

    (result,) = check_client_build()
    assert result.status == "ok"


def test_an_installed_app_has_no_client_build_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_assets, "_development_checkout", lambda: None)

    assert check_client_build() == []
