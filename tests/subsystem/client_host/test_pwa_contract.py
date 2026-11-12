from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image

from row_bot.access.policy import APP_INSTALL_PATHS


ROOT = Path(__file__).resolve().parents[3]
PUBLIC = ROOT / "frontend/public"


def _manifest() -> dict:
    return json.loads((PUBLIC / "app.webmanifest").read_text(encoding="utf-8"))


def test_manifest_is_owned_scoped_and_uses_local_icons() -> None:
    manifest = _manifest()
    assert manifest["id"] == manifest["start_url"] == manifest["scope"] == "/app-v2/"
    assert manifest["display"] == "standalone"
    # Android crops a maskable icon; the plain ones are never offered for that.
    assert [(icon["src"], icon["sizes"], icon["purpose"]) for icon in manifest["icons"]] == [
        ("/app-v2/icon-192.png", "192x192", "any"),
        ("/app-v2/icon-512.png", "512x512", "any"),
        ("/app-v2/icon-maskable-192.png", "192x192", "maskable"),
        ("/app-v2/icon-maskable-512.png", "512x512", "maskable"),
    ]
    assert [(item["name"], item["url"]) for item in manifest["shortcuts"]] == [
        ("New chat", "/app-v2/?new=chat"),
        ("Home", "/app-v2/"),
    ]
    assert [item["form_factor"] for item in manifest["screenshots"]] == ["narrow", "wide"]


def test_everything_the_manifest_lists_is_local_sized_and_installable_before_sign_in() -> None:
    manifest = _manifest()
    images = [
        *manifest["icons"],
        *(icon for item in manifest["shortcuts"] for icon in item["icons"]),
        *manifest["screenshots"],
    ]
    for image in images:
        assert image["src"] in APP_INSTALL_PATHS
        with Image.open(PUBLIC / Path(image["src"]).name) as opened:
            assert "x".join(map(str, opened.size)) == image["sizes"]


def test_maskable_icons_are_opaque_with_the_mark_inside_the_safe_zone() -> None:
    for name in ("icon-maskable-192.png", "icon-maskable-512.png"):
        with Image.open(PUBLIC / name) as opened:
            icon = opened.convert("RGBA")
        size = icon.width
        assert icon.getchannel("A").getextrema() == (255, 255)
        background = icon.getpixel((0, 0))
        radius = 0.4 * size  # the W3C safe zone: a centred circle 80% across
        for y in range(size):
            for x in range(size):
                if math.hypot(x + 0.5 - size / 2, y + 0.5 - size / 2) > radius:
                    assert icon.getpixel((x, y)) == background, (name, x, y)


def test_service_worker_caches_only_same_origin_immutable_build_assets() -> None:
    source = (PUBLIC / "service-worker.js").read_text(encoding="utf-8")
    assert "request.method !== 'GET'" in source
    assert "url.origin !== self.location.origin" in source
    assert "IMMUTABLE_ASSET.test(url.pathname)" in source
    assert "response.type === 'basic'" in source
    assert "immutable" in source
    assert "SKIP_WAITING" in source
    assert "fetch(request).catch(offline)" in source
    for private_boundary in (
        "/api/",
        "transcript",
        "credential",
        "approval",
        "media",
        "localStorage",
        "sessionStorage",
    ):
        assert private_boundary not in source
