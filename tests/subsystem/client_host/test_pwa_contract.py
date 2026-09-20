from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_manifest_is_owned_scoped_and_uses_local_icons() -> None:
    manifest = json.loads((ROOT / "frontend/public/app.webmanifest").read_text(encoding="utf-8"))
    assert manifest["id"] == manifest["start_url"] == manifest["scope"] == "/app-v2/"
    assert manifest["display"] == "standalone"
    assert [(icon["src"], icon["sizes"]) for icon in manifest["icons"]] == [
        ("/app-v2/icon-192.png", "192x192"),
        ("/app-v2/icon-512.png", "512x512"),
    ]
    assert all((ROOT / "frontend/public" / Path(icon["src"]).name).is_file() for icon in manifest["icons"])


def test_service_worker_caches_only_same_origin_immutable_build_assets() -> None:
    source = (ROOT / "frontend/public/service-worker.js").read_text(encoding="utf-8")
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
