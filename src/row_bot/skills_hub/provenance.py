"""Provenance lockfile for hub-installed skills."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import SkillInstallRecord


def hub_dir() -> Path:
    import row_bot.skills as skills

    path = skills.USER_SKILLS_DIR / ".hub"
    path.mkdir(parents=True, exist_ok=True)
    return path


def lockfile_path() -> Path:
    return hub_dir() / "lock.json"


def audit_log_path() -> Path:
    return hub_dir() / "audit.log"


def quarantine_dir() -> Path:
    path = hub_dir() / "quarantine"
    path.mkdir(parents=True, exist_ok=True)
    return path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_records() -> dict[str, SkillInstallRecord]:
    path = lockfile_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    records = raw.get("records", raw if isinstance(raw, dict) else {})
    if not isinstance(records, dict):
        return {}
    parsed: dict[str, SkillInstallRecord] = {}
    for name, data in records.items():
        if isinstance(data, dict):
            try:
                parsed[str(name)] = SkillInstallRecord.from_dict(data)
            except Exception:
                continue
    return parsed


def save_records(records: dict[str, SkillInstallRecord]) -> None:
    path = lockfile_path()
    payload = {
        "schema_version": 1,
        "updated_at": now_iso(),
        "records": {name: record.as_dict() for name, record in sorted(records.items())},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def get_record(local_name: str) -> SkillInstallRecord | None:
    return load_records().get(local_name)


def upsert_record(record: SkillInstallRecord) -> None:
    records = load_records()
    records[record.local_name] = record
    save_records(records)


def remove_record(local_name: str) -> SkillInstallRecord | None:
    records = load_records()
    record = records.pop(local_name, None)
    save_records(records)
    return record


def hub_installed_count() -> int:
    return len(load_records())


def append_audit(event: str, **data: Any) -> None:
    payload = {"ts": now_iso(), "event": event, **data}
    path = audit_log_path()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
