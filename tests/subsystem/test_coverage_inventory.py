from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.coverage_inventory import (
    ALLOWED_STATUSES,
    COVERAGE_INVENTORY,
    LEGACY_COVERAGE_FILES,
    PRIORITY_SUBSYSTEMS,
    inventory_by_subsystem,
)


pytestmark = pytest.mark.subsystem


def test_priority_subsystems_have_replacement_inventory() -> None:
    inventory = inventory_by_subsystem()
    assert PRIORITY_SUBSYSTEMS <= set(inventory)
    for subsystem in PRIORITY_SUBSYSTEMS:
        entries = inventory[subsystem]
        replacements = {path for entry in entries for path in entry.replacement_files}
        assert replacements, subsystem
        assert any(path.startswith(("tests/contracts/", "tests/subsystem/")) for path in replacements), subsystem


def test_legacy_coverage_files_are_retained_and_mapped() -> None:
    mapped_legacy = {path for entry in COVERAGE_INVENTORY for path in entry.legacy_files}
    assert LEGACY_COVERAGE_FILES <= mapped_legacy
    for path in LEGACY_COVERAGE_FILES:
        assert Path(path).exists(), path


def test_inventory_entries_are_machine_checkable() -> None:
    for entry in COVERAGE_INVENTORY:
        assert entry.status in ALLOWED_STATUSES
        assert entry.subsystem
        assert entry.replacement_files
        for replacement in entry.replacement_files:
            assert replacement.startswith(("tests/", "scripts/")), replacement
        for invariant in entry.invariants:
            assert invariant.strip()
