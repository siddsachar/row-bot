"""Migration wizard foundation.

Phase 1 exposes pure data models and redaction helpers. Source detection,
planning adapters, apply logic, backups, and UI are layered on top later.
"""

from migration.core import (
    ConflictPolicy,
    MigrationAction,
    MigrationCategory,
    MigrationItem,
    MigrationPlan,
    MigrationProvider,
    MigrationSensitivity,
    MigrationSource,
    MigrationStatus,
    PlanSummary,
    SOURCE_DEFAULTS,
    make_item_id,
    normalize_provider,
)
from migration.apply import MigrationApplyOptions, MigrationApplyResult, apply_migration_plan
from migration.detection import (
    HERMES_ARCHIVE_DIRS,
    HERMES_ARCHIVE_FILES,
    OPENCLAW_ARCHIVE_DIRS,
    OPENCLAW_ARCHIVE_FILES,
    MigrationScan,
    MigrationScanEntry,
    MigrationScanKind,
    MigrationScanSummary,
    detect_hermes_source,
    detect_openclaw_source,
    detect_source,
)
from migration.planner import build_hermes_plan, build_migration_plan, build_openclaw_plan
from migration.redaction import REDACTED, is_sensitive_key, redact_mapping, redact_value

__all__ = [
    "ConflictPolicy",
    "HERMES_ARCHIVE_DIRS",
    "HERMES_ARCHIVE_FILES",
    "MigrationAction",
    "MigrationApplyOptions",
    "MigrationApplyResult",
    "MigrationCategory",
    "MigrationItem",
    "MigrationPlan",
    "MigrationProvider",
    "MigrationScan",
    "MigrationScanEntry",
    "MigrationScanKind",
    "MigrationScanSummary",
    "MigrationSensitivity",
    "MigrationSource",
    "MigrationStatus",
    "OPENCLAW_ARCHIVE_DIRS",
    "OPENCLAW_ARCHIVE_FILES",
    "PlanSummary",
    "REDACTED",
    "SOURCE_DEFAULTS",
    "apply_migration_plan",
    "build_hermes_plan",
    "build_migration_plan",
    "build_openclaw_plan",
    "detect_hermes_source",
    "detect_openclaw_source",
    "detect_source",
    "is_sensitive_key",
    "make_item_id",
    "normalize_provider",
    "redact_mapping",
    "redact_value",
]
