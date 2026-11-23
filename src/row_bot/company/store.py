"""What a Row-Bot joined to a company keeps.

- The credential (the device's access and refresh tokens) lives in the system keychain only, in one entry, so
  a new pair replaces the old one at once. It is never written to a file, a log or an API response.
- The record holds nothing secret: the server's origin, the device id, the company, the person's name and
  email, the computer's name as the server knows it, when it joined, and whether the company disconnected it.
  It is ``company.json`` in the data folder.

No record means no company: reading it never touches the keychain or the network.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from row_bot import secret_store
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

RECORD_FILE = "company.json"
NAMESPACE = "company"
CREDENTIAL = "device"
MAX_RECORD_BYTES = 16 * 1024


class StorageUnavailable(RuntimeError):
    """The keychain could not keep the credential; nothing was written anywhere else."""


@dataclass(frozen=True)
class Record:
    origin: str
    device_id: str
    company: str
    name: str
    email: str
    device_name: str
    joined_at: str
    disconnected: bool = False


@dataclass(frozen=True)
class Credential:
    access_token: str
    refresh_token: str
    # When the access token ends, in seconds since the epoch.
    expires_at: float


def record_path() -> Path:
    return get_row_bot_data_dir(create=False) / RECORD_FILE


def read_record() -> Record | None:
    """The joined company, or None (none joined, or a record that can't be read)."""
    path = record_path()
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_RECORD_BYTES:
            raise ValueError("too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("unknown record")
        fields = {name: data[name] for name in Record.__dataclass_fields__}
        if not all(isinstance(value, str) for name, value in fields.items() if name != "disconnected"):
            raise ValueError("unexpected field")
        if not isinstance(fields["disconnected"], bool):
            raise ValueError("unexpected field")
        return Record(**fields)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        logger.warning("The company record could not be read; this Row-Bot shows as not joined")
        return None


def write_record(record: Record) -> None:
    """Replace the record in one step (a reader sees the old one or the new one)."""
    path = record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps({"schema_version": 1, **asdict(record)}, indent=2) + "\n",
                       encoding="utf-8", newline="\n")
    os.replace(partial, path)


def mark_disconnected() -> None:
    record = read_record()
    if record is not None and not record.disconnected:
        write_record(replace(record, disconnected=True))


def delete_record() -> None:
    record_path().unlink(missing_ok=True)


def storage_available() -> bool:
    """Whether the keychain can keep a credential now (a probe write, read and delete)."""
    return secret_store.is_available()


def read_credential() -> Credential | None:
    """The credential, or None when there is none; raises StorageUnavailable when the keychain can't be read
    (locked, say), which is not the same as the company letting the device go."""
    try:
        text = secret_store.get_secret(CREDENTIAL, namespace=NAMESPACE)
    except secret_store.SecretStoreError:
        raise StorageUnavailable("the keychain could not be read") from None
    if not text:
        return None
    try:
        data = json.loads(text)
        return Credential(access_token=str(data["access_token"]), refresh_token=str(data["refresh_token"]),
                          expires_at=float(data["expires_at"]))
    except (ValueError, KeyError, TypeError):
        logger.warning("The company credential in the keychain could not be read")
        return None


def save_credential(credential: Credential) -> None:
    """Keep a new pair in the keychain, replacing the old one; raises StorageUnavailable if it can't."""
    try:
        secret_store.set_secret(CREDENTIAL, json.dumps(asdict(credential)), namespace=NAMESPACE)
    except secret_store.SecretStoreError:
        raise StorageUnavailable("the keychain could not keep the company credential") from None


def delete_credential() -> None:
    try:
        secret_store.delete_secret(CREDENTIAL, namespace=NAMESPACE)
    except secret_store.SecretStoreError:
        logger.warning("The company credential could not be removed from the keychain")
