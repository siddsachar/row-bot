from __future__ import annotations

from row_bot.computer_use.readiness import load_cua_manifest


def test_reviewed_manifest_is_exact_and_never_latest() -> None:
    manifest = load_cua_manifest()
    assert manifest["version"] == "0.7.1"
    assert manifest["tag"] == "cua-driver-rs-v0.7.1"
    assert manifest["commit"] == "7caf72b"
    assert manifest["license"] == "MIT"
    for asset in manifest["assets"].values():
        assert len(asset["sha256"]) == 64
        assert "/cua-driver-rs-v0.7.1/" in asset["url"]
        assert "latest" not in asset["url"]
