"""A snapshot comparison fails when the snapshot file is missing (B198).

The helper used to write a missing snapshot and pass, so deleting a snapshot (or
mistyping its path) silently turned the check into a no-op. Recording one is an
explicit opt-in: ROW_BOT_UPDATE_SNAPSHOTS=1.
"""
from __future__ import annotations

import pytest

from tests.helpers.snapshots import assert_matches_snapshot


pytestmark = pytest.mark.subsystem


def test_a_missing_snapshot_fails_without_writing_one(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ROW_BOT_UPDATE_SNAPSHOTS", raising=False)
    snapshot = tmp_path / "missing.snapshot"

    with pytest.raises(AssertionError, match="ROW_BOT_UPDATE_SNAPSHOTS=1"):
        assert_matches_snapshot(snapshot, "content\n")
    assert not snapshot.exists()


def test_a_snapshot_is_recorded_only_on_request(tmp_path, monkeypatch) -> None:
    snapshot = tmp_path / "designer" / "recorded.snapshot"
    monkeypatch.setenv("ROW_BOT_UPDATE_SNAPSHOTS", "1")

    assert_matches_snapshot(snapshot, "first\r\n")

    monkeypatch.delenv("ROW_BOT_UPDATE_SNAPSHOTS")
    assert snapshot.read_text(encoding="utf-8") == "first\n"
    assert_matches_snapshot(snapshot, "first\n")
    with pytest.raises(AssertionError):
        assert_matches_snapshot(snapshot, "changed\n")
