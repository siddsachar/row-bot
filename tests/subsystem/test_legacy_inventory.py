from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.legacy_inventory import LEGACY_FILES, LEGACY_STATUSES, build_legacy_inventory


pytestmark = pytest.mark.subsystem


def test_legacy_inventory_extracts_section_level_entries() -> None:
    inventory = build_legacy_inventory()

    assert inventory
    assert {entry.legacy_file for entry in inventory} == set(LEGACY_FILES)
    assert any(entry.legacy_file == "tests/test_suite.py" and "AST SYNTAX CHECK" in entry.heading for entry in inventory)
    assert any(entry.legacy_file == "tests/integration_tests.py" and "SECTION 20" in entry.heading for entry in inventory)
    assert any(entry.legacy_file == "tests/test_memory_e2e.py" and "SECTION 16" in entry.heading for entry in inventory)


def test_legacy_inventory_entries_are_mapped_and_machine_checkable() -> None:
    inventory = build_legacy_inventory()

    for entry in inventory:
        assert entry.status in LEGACY_STATUSES
        assert entry.subsystem
        assert entry.replacement_paths
        assert entry.verification_command.startswith("uv run python")
        assert entry.start_line > 0
        assert entry.end_line >= entry.start_line


def test_covered_legacy_targets_point_to_existing_replacements() -> None:
    inventory = build_legacy_inventory()

    for entry in inventory:
        if entry.status != "covered":
            continue
        assert any(Path(path).exists() for path in entry.replacement_paths), entry
