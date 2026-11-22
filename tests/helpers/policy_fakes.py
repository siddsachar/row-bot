"""Fakes for the organisation policy loader: a ``System`` whose every source lives under a test folder, a
registry built from a policy document, and an Ed25519 signer that pins its key beside a policy file."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from row_bot.policy.sources import KEYS_FILE, REG_DWORD, REG_SZ, Platform, RegistryKey, System


def trusting(path: Path, boundary: Path | None) -> str | None:
    """A trust check that accepts every file, standing in for an administrator-owned location."""
    return None


def fake_system(root: Path, platform: Platform = "linux", *, registry: RegistryKey | None = None,
                environ: dict[str, str] | None = None,
                trust: Callable[[Path, Path | None], str | None] = trusting) -> System:
    return System(
        platform, environ or {}, registry=lambda: registry, program_data=lambda: root / "ProgramData",
        mac_managed_preferences=root / "Managed Preferences" / "ai.row-bot.plist",
        mac_policy_file=root / "Application Support" / "Row-Bot" / "org-policy.json",
        linux_policy_file=root / "etc" / "row-bot" / "org-policy.json", trust=trust,
    )


def windows_policy_file(root: Path) -> Path:
    return root / "ProgramData" / "Row-Bot" / "policy" / "org-policy.json"


def write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def write_policy(path: Path, document: object) -> Path:
    return write_bytes(path, json.dumps(document, indent=2).encode())


def registry_from(document: dict[str, Any]) -> RegistryKey:
    """The registry values an administrator would set for *document* (as the ADMX writes them)."""
    def value(item: object) -> tuple[int, object]:
        if isinstance(item, bool):
            return REG_DWORD, int(item)
        if isinstance(item, int):
            return REG_DWORD, item
        return REG_SZ, item

    sections = {}
    for name, settings in document.items():
        if not isinstance(settings, dict):
            continue
        values = {key: value(item) for key, item in settings.items() if not isinstance(item, list)}
        lists = {key: RegistryKey({str(index): (REG_SZ, entry) for index, entry in enumerate(item, 1)})
                 for key, item in settings.items() if isinstance(item, list)}
        sections[name] = RegistryKey(values, lists)
    root = {key: value(item) for key, item in document.items() if not isinstance(item, dict)}
    return RegistryKey(root, sections)


class Signer:
    """An organisation signing key."""

    def __init__(self, key_id: str = "it-2027"):
        self.key_id = key_id
        self._key = Ed25519PrivateKey.generate()

    def public(self) -> str:
        raw = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode()

    def signature(self, data: bytes) -> bytes:
        return base64.b64encode(self._key.sign(data))

    def sign(self, policy: Path) -> Path:
        return write_bytes(policy.with_name(policy.name + ".sig"), self.signature(policy.read_bytes()))


def pin(policy: Path, *signers: Signer) -> Path:
    """Write the keys file beside *policy*, pinning *signers*."""
    return write_policy(policy.parent / KEYS_FILE, {
        "schema_version": 1, "keys": [{"id": signer.key_id, "ed25519": signer.public()} for signer in signers]})
