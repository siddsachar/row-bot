"""Independent publication review with isolated synthetic runtime manifests."""
import pytest

from row_bot.mcp_client import requirements as runtime

pytestmark = pytest.mark.subsystem


def test_manifest_change_between_authority_and_snapshot_is_not_overwritten(tmp_path, monkeypatch):
    from row_bot.developer import edits

    monkeypatch.setattr(runtime, "RUNTIMES_DIR", tmp_path / "runtimes")
    runtime._write_manifest("synthetic", {"version": "original"})
    revision = runtime.runtime_install_revision("synthetic")
    manifest = runtime.RUNTIMES_DIR / "synthetic" / "manifest.json"
    external = b'{"version":"external replacement"}'
    read = edits.read_edit_bytes
    replaced = False

    def race(*args, **kwargs):
        nonlocal replaced
        if not replaced:
            replaced = True
            manifest.write_bytes(external)
        return read(*args, **kwargs)

    monkeypatch.setattr(edits, "read_edit_bytes", race)
    with pytest.raises(RuntimeError, match="revision changed"):
        runtime._write_manifest("synthetic", {"version": "new"}, expected_revision=revision)
    assert manifest.read_bytes() == external
