"""Focused tests for Phase 4 migration apply, backup, and reports."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pytest


pytestmark = pytest.mark.platform


class _FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


def _install_fake_keyring():
    import row_bot.secret_store as secret_store
    backend = _FakeKeyring()
    secret_store._set_backend_for_tests(backend)
    return secret_store


class MigrationApplyTests(unittest.TestCase):
    def test_applies_selected_hermes_items_and_archives_report_only_state(self) -> None:
        from row_bot.migration import MigrationStatus, apply_migration_plan, build_hermes_plan
        from row_bot.migration.fixtures import create_realistic_hermes_home

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = create_realistic_hermes_home(root / ".hermes")
            target = root / "target"
            plan = build_hermes_plan(source, target_root=target)
            result = apply_migration_plan(plan)

            self.assertEqual(result.summary.errors, 0)
            config = json.loads((target / "user_config.json").read_text(encoding="utf-8"))
            self.assertIn("Calm, direct", config["identity"]["personality"])
            self.assertTrue((target / "skills" / "ship-it" / "SKILL.md").is_file())
            self.assertTrue((target / "skills" / "instructions-from-hermes" / "SKILL.md").is_file())
            self.assertEqual(json.loads((target / "model_settings.json").read_text(encoding="utf-8"))["model"],
                             "model:openai:gpt-5.4")
            # Nothing lands in side files that Row-Bot never reads.
            for dead in ("identity", "memory", "config"):
                self.assertFalse((target / dead).exists(), dead)
            self.assertFalse((target / "api_keys.json").exists())
            self.assertTrue((result.report_dir / "archive" / "logs" / "hermes.log").is_file())
            self.assertTrue((result.report_dir / "plan.json").is_file())
            self.assertTrue((result.report_dir / "result.json").is_file())
            self.assertTrue(any(item.status == MigrationStatus.MIGRATED for item in result.items))

            report_text = (result.report_dir / "result.json").read_text(encoding="utf-8")
            archive_auth_text = (result.report_dir / "archive" / "auth.json").read_text(encoding="utf-8")
            archive_token_text = (result.report_dir / "archive" / "mcp-tokens" / "linear.json").read_text(encoding="utf-8")
            self.assertNotIn("sk-hermes-three-month-user", report_text)
            self.assertNotIn("linear-secret-token", report_text)
            self.assertNotIn("sk-legacy-auth", archive_auth_text)
            self.assertNotIn("linear-secret-token", archive_token_text)
            self.assertIn("[redacted]", archive_auth_text)
            self.assertIn("[redacted]", archive_token_text)

    def test_existing_targets_are_backed_up_before_overwrite(self) -> None:
        from row_bot.migration import apply_migration_plan, build_hermes_plan
        from row_bot.migration.fixtures import create_realistic_hermes_home

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = create_realistic_hermes_home(root / ".hermes")
            target = root / "target"
            plan = build_hermes_plan(source, target_root=target)
            existing = {"identity": {"name": "Rowan", "personality": "Existing persona."}, "theme": "dark"}
            _write(target / "user_config.json", json.dumps(existing))
            result = apply_migration_plan(plan)

            self.assertTrue(result.backup_dir)
            backup = result.backup_dir / "user_config.json"
            self.assertTrue(backup.is_file())
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), existing)
            saved = json.loads((target / "user_config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["theme"], "dark")
            self.assertEqual(saved["identity"]["name"], "Rowan")
            self.assertNotEqual(saved["identity"]["personality"], "Existing persona.")
            self.assertTrue(result.backup_manifest)

    def test_secret_apply_requires_explicit_secret_plan(self) -> None:
        from row_bot.migration import apply_migration_plan, build_hermes_plan
        from row_bot.migration.fixtures import create_realistic_hermes_home
        from row_bot.api_keys import get_key_for_data_dir

        secret_store = _install_fake_keyring()

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source = create_realistic_hermes_home(root / ".hermes")
                target = root / "target"
                plan = build_hermes_plan(source, target_root=target, include_secrets=True)
                result = apply_migration_plan(plan)

                api_metadata = json.loads((target / "api_keys.json").read_text(encoding="utf-8"))
                self.assertEqual(get_key_for_data_dir(target, "OPENAI_API_KEY"), "sk-hermes-three-month-user")
                self.assertEqual(api_metadata["version"], 2)
                self.assertNotIn("sk-hermes-three-month-user", json.dumps(api_metadata))
                api_key_backups = [entry for entry in result.backup_manifest if entry["source"].endswith("api_keys.json")]
                self.assertEqual(api_key_backups, [])
                result_text = json.dumps(result.to_dict())
                self.assertNotIn("sk-hermes-three-month-user", result_text)
        finally:
            secret_store._set_backend_for_tests(None)

    def test_openclaw_memory_files_and_daily_notes_go_to_review_together(self) -> None:
        import os
        from unittest import mock

        from row_bot.migration import MigrationApplyOptions, MigrationStatus, apply_migration_plan, build_openclaw_plan
        from row_bot.migration.fixtures import create_realistic_openclaw_home

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = create_realistic_openclaw_home(root / ".openclaw")
            target = root / "profile"
            target.mkdir()
            staged: list[list[str]] = []
            # The target is this Row-Bot's own data folder, where memories go to be reviewed.
            with mock.patch.dict(os.environ, {"ROW_BOT_DATA_DIR": str(target)}):
                plan = build_openclaw_plan(source, target_root=target)
                memories = [item.id for item in plan.items if item.category.value == "memories"]
                result = apply_migration_plan(plan, MigrationApplyOptions(stage_memories=lambda files: (
                    staged.append(sorted(path.name for path in files)) or {"batch_id": "a" * 32, "found": 7})))

            self.assertEqual(memories, ["memories:memory.md", "memories:user.md", "memories:daily-notes"])
            self.assertEqual(staged, [["2026-02-27.md", "2026-03-19.md", "2026-04-11.md", "MEMORY.md", "USER.md"]])
            self.assertEqual(result.memory_review, {"batch_id": "a" * 32, "found": 7, "conversations": 0})
            self.assertTrue(all(item.status == MigrationStatus.MIGRATED for item in result.items
                                if item.category.value == "memories"))
            self.assertFalse((target / "memory").exists())

    def test_memories_are_not_applied_without_the_review_queue(self) -> None:
        from row_bot.migration import MigrationStatus, apply_migration_plan, build_hermes_plan
        from row_bot.migration.fixtures import create_realistic_hermes_home

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = create_realistic_hermes_home(root / ".hermes")
            plan = build_hermes_plan(source, target_root=root / "target")
            memories = [item for item in plan.items if item.category.value == "memories"]
            # Somewhere else than this Row-Bot's data folder they are listed, never chosen.
            self.assertTrue(memories)
            self.assertTrue(all(item.action.value == "manual_review" and not item.selected for item in memories))
            chosen = plan.__class__(source=plan.source, items=tuple(item.with_selection(True) for item in memories),
                                    metadata=dict(plan.metadata))
            result = apply_migration_plan(chosen)

        self.assertTrue(all(item.status == MigrationStatus.SKIPPED for item in result.items))
        self.assertIsNone(result.memory_review)

    def test_multiple_secret_writes_backup_original_api_key_file_once(self) -> None:
        from row_bot.migration import apply_migration_plan, build_hermes_plan
        from row_bot.migration.fixtures import create_realistic_hermes_home
        from row_bot.api_keys import get_key_for_data_dir

        secret_store = _install_fake_keyring()

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source = create_realistic_hermes_home(root / ".hermes")
                target = root / "target"
                original_api_keys = {"OPENAI_API_KEY": "sk-existing-row-bot-fake"}
                _write(target / "api_keys.json", json.dumps(original_api_keys, indent=2) + "\n")
                plan = build_hermes_plan(source, target_root=target, include_secrets=True)
                result = apply_migration_plan(plan)

                api_metadata = json.loads((target / "api_keys.json").read_text(encoding="utf-8"))
                backup = json.loads((result.backup_dir / "api_keys.json").read_text(encoding="utf-8"))
                api_key_backups = [entry for entry in result.backup_manifest if entry["source"].endswith("api_keys.json")]

            self.assertEqual(backup, original_api_keys)
            self.assertEqual(len(api_key_backups), 1)
            self.assertEqual(api_metadata["version"], 2)
            self.assertNotIn("sk-hermes-three-month-user", json.dumps(api_metadata))
            self.assertEqual(get_key_for_data_dir(target, "ANTHROPIC_API_KEY"), "sk-ant-hermes-example")
            self.assertEqual(get_key_for_data_dir(target, "TELEGRAM_BOT_TOKEN"), "123456:hermes-demo-token")
            self.assertEqual(get_key_for_data_dir(target, "OPENAI_API_KEY"), "sk-hermes-three-month-user")
        finally:
            secret_store._set_backend_for_tests(None)

    def test_apply_requires_backup_unless_explicitly_overridden(self) -> None:
        from row_bot.migration import MigrationApplyOptions, apply_migration_plan, build_hermes_plan
        from row_bot.migration.fixtures import create_realistic_hermes_home

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = create_realistic_hermes_home(root / ".hermes")
            plan = build_hermes_plan(source, target_root=root / "target")
            with self.assertRaises(ValueError):
                apply_migration_plan(plan, MigrationApplyOptions(require_backup=False))
            result = apply_migration_plan(plan, MigrationApplyOptions(require_backup=False, allow_without_backup=True))

        self.assertIsNone(result.backup_dir)
        self.assertTrue(result.report_dir)

    def test_item_errors_do_not_stop_report_generation(self) -> None:
        from row_bot.migration import (
            MigrationAction,
            MigrationCategory,
            MigrationItem,
            MigrationPlan,
            MigrationSource,
            MigrationStatus,
            apply_migration_plan,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = MigrationSource.from_path("hermes", root / ".hermes", found=True, confidence="high")
            good_source = root / "source.md"
            _write(good_source, "# Good\n")
            plan = MigrationPlan(
                source=source,
                items=(
                    MigrationItem(
                        id="identity:missing",
                        category=MigrationCategory.IDENTITY,
                        action=MigrationAction.COPY,
                        source=root / "missing.md",
                        target=root / "target" / "missing.md",
                    ),
                    MigrationItem(
                        id="identity:good",
                        category=MigrationCategory.IDENTITY,
                        action=MigrationAction.COPY,
                        source=good_source,
                        target=root / "target" / "good.md",
                    ),
                ),
                metadata={"target_root": str(root / "target")},
            )
            result = apply_migration_plan(plan)

            self.assertTrue((root / "target" / "good.md").is_file())
            self.assertTrue((result.report_dir / "result.json").is_file())
            self.assertTrue(any(item.status == MigrationStatus.ERROR for item in result.items))
            self.assertEqual(result.summary.errors, 1)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
