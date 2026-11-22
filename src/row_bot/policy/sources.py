"""Where an organisation policy comes from, and how one is read, verified and turned into the effective policy.

Sources, checked in this order; the first one present is used and the others are reported as ignored:

1. Windows: ``HKLM\\SOFTWARE\\Policies\\Row-Bot`` (values as the ADMX writes them), then
   ``%ProgramData%\\Row-Bot\\policy\\org-policy.json``.
2. macOS: the managed preferences of ``ai.row-bot`` (``/Library/Managed Preferences/ai.row-bot.plist``, as a
   configuration profile writes it), then ``/Library/Application Support/Row-Bot/org-policy.json``.
3. Linux: ``/etc/row-bot/org-policy.json``.
4. Anywhere: the file ``ROW_BOT_POLICY_FILE`` names (servers and containers).

Checking for a source costs one stat (one registry open on Windows). A JSON file may be signed: a detached
Ed25519 signature in ``<file>.sig`` (base64) by a key pinned in ``org-policy-keys.json`` beside it. While that
keys file is present, or when the policy sets ``require_signature``, an unsigned file is refused.

Anything present but unreadable, untrusted, invalid or badly signed gives ``Lockdown``; nothing present gives
``NoPolicy``. The OS is reached only through ``System``, so tests run every path with fakes.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from row_bot.policy.model import IgnoredSource, Lockdown, NoPolicy, OrgPolicy, Policy, PolicySource
from row_bot.policy.schema import SECTIONS, InvalidPolicy, parse, parse_keys, specs
from row_bot.policy.trust import trust_problem

logger = logging.getLogger(__name__)

REGISTRY_KEY = r"SOFTWARE\Policies\Row-Bot"
MAC_DOMAIN = "ai.row-bot"
MAC_MANAGED_PREFERENCES = Path("/Library/Managed Preferences") / f"{MAC_DOMAIN}.plist"
MAC_POLICY_FILE = Path("/Library/Application Support/Row-Bot/org-policy.json")
LINUX_POLICY_FILE = Path("/etc/row-bot/org-policy.json")
POLICY_FILE_ENV = "ROW_BOT_POLICY_FILE"
KEYS_FILE = "org-policy-keys.json"
MAX_BYTES = 256 * 1024
REG_SZ, REG_DWORD = 1, 4
LIST_MARKER = "_set"  # ``<list>_set`` = 1: the admin set this list, so no entries means an empty list

Platform = Literal["windows", "macos", "linux"]


@dataclass(frozen=True)
class RegistryKey:
    """A registry key as read: its values as ``name -> (type, data)`` and its subkeys."""

    values: Mapping[str, tuple[int, object]] = field(default_factory=dict)
    subkeys: Mapping[str, RegistryKey] = field(default_factory=dict)

    def empty(self) -> bool:
        return not self.values and all(key.empty() for key in self.subkeys.values())


@dataclass(frozen=True)
class System:
    """The operating system as the loader sees it. ``os_system()`` is the real one; tests build fakes."""

    platform: Platform
    environ: Mapping[str, str]
    registry: Callable[[], RegistryKey | None] = lambda: None
    program_data: Callable[[], Path] | None = None  # Windows: %ProgramData%
    mac_managed_preferences: Path = MAC_MANAGED_PREFERENCES
    mac_policy_file: Path = MAC_POLICY_FILE
    linux_policy_file: Path = LINUX_POLICY_FILE
    trust: Callable[[Path, Path | None], str | None] = trust_problem


def os_system() -> System:
    """This computer."""
    if sys.platform == "win32":
        return System("windows", os.environ, registry=read_registry, program_data=_program_data)
    return System("macos" if sys.platform == "darwin" else "linux", os.environ)


# --- Discovery ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Candidate:
    source: PolicySource
    path: Path | None = None
    boundary: Path | None = None
    registry: RegistryKey | None = None
    error: str = ""


def _candidates(system: System) -> list[_Candidate]:
    found: list[_Candidate] = []

    def file(kind: Literal["file", "env_file"], path: Path, boundary: Path | None = None) -> None:
        found.append(_Candidate(PolicySource(kind, str(path)), path=path, boundary=boundary))

    if system.platform == "windows":
        location = f"HKLM\\{REGISTRY_KEY}"
        try:
            tree = system.registry()
        except OSError as exc:
            found.append(_Candidate(PolicySource("registry", location), error=exc.strerror or str(exc)))
        else:
            if tree is not None and not tree.empty():
                found.append(_Candidate(PolicySource("registry", location), registry=tree))
        if system.program_data is not None:
            try:
                program_data = system.program_data()
            except OSError as exc:
                found.append(_Candidate(PolicySource("file", r"%ProgramData%\Row-Bot\policy\org-policy.json"),
                                        error=str(exc)))
            else:
                path = program_data / "Row-Bot" / "policy" / "org-policy.json"
                if os.path.lexists(path):
                    file("file", path, program_data)
    elif system.platform == "macos":
        if os.path.lexists(system.mac_managed_preferences):
            found.append(_Candidate(PolicySource("managed_preferences", str(system.mac_managed_preferences)),
                                    path=system.mac_managed_preferences))
        if os.path.lexists(system.mac_policy_file):
            file("file", system.mac_policy_file)
    elif os.path.lexists(system.linux_policy_file):
        file("file", system.linux_policy_file)
    named = (system.environ.get(POLICY_FILE_ENV) or "").strip()
    if named:
        # Named on purpose, so a missing file is a broken policy, not an absent one.
        path = Path(named)
        file("env_file", path, Path(path.anchor) if system.platform == "windows" else None)
    return found


def load_policy(system: System) -> Policy:
    """The effective policy on *system*."""
    candidates = _candidates(system)
    if not candidates:
        return NoPolicy()
    chosen, rest = candidates[0], candidates[1:]
    ignored = tuple(IgnoredSource(candidate.source, "a higher-precedence source is used") for candidate in rest)
    if ignored:
        logger.info("Organisation policy from %s; ignoring %s", chosen.source.location,
                    ", ".join(item.source.location for item in ignored))
    try:
        settings, signer = _read(system, chosen)
    except InvalidPolicy as exc:
        reason, detail = exc.code, exc.detail
    except Exception:  # a policy is present: whatever went wrong reading it, fail closed (GHSA-68j6-243q-h2hp)
        logger.exception("Reading the organisation policy at %s failed", chosen.source.location)
        reason, detail = "unreadable", f"{chosen.source.location}: Row-Bot couldn't read it"
    else:
        return OrgPolicy(settings=settings, source=chosen.source, signer=signer, ignored=ignored)
    logger.warning("Organisation policy at %s can't be used (%s): %s. Row-Bot is locked down.",
                   chosen.source.location, reason, detail)
    return Lockdown(reason=reason, detail=detail, source=chosen.source, ignored=ignored)


# --- Reading one source -------------------------------------------------------------------------------------

def _read(system: System, candidate: _Candidate) -> tuple[Any, str | None]:
    if candidate.error:
        raise InvalidPolicy("unreadable", f"{candidate.source.location}: {candidate.error}")
    if candidate.registry is not None:
        return _unsigned(parse(document_from_registry(candidate.registry)), "registry")
    assert candidate.path is not None
    if candidate.source.kind == "env_file" and not candidate.path.is_absolute():
        raise InvalidPolicy("untrusted_location", f"{POLICY_FILE_ENV}: {candidate.path} is not an absolute path")
    data = _trusted_bytes(system, candidate.path, candidate.boundary)
    if candidate.source.kind == "managed_preferences":
        return _unsigned(parse(_plist(data, candidate.path)), "managed preferences")
    return _signed_file(system, candidate, data)


def _unsigned(parsed: tuple[Any, bool], where: str) -> tuple[Any, None]:
    settings, require_signature = parsed
    if require_signature:
        raise InvalidPolicy("signature_missing", f"require_signature is set, but a policy in {where} can't be signed")
    return settings, None


def _trusted_bytes(system: System, path: Path, boundary: Path | None) -> bytes:
    if not os.path.lexists(path):
        raise InvalidPolicy("unreadable", f"{path}: not found")
    problem = system.trust(path, boundary)
    if problem:
        raise InvalidPolicy("untrusted_location", problem)
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise InvalidPolicy("unreadable", f"{path}: {exc.strerror or exc.__class__.__name__}") from None
    if len(data) > MAX_BYTES:
        raise InvalidPolicy("invalid", f"{path}: larger than {MAX_BYTES // 1024} KiB")
    return data


def _json(data: bytes, path: Path, code: str = "invalid") -> object:
    import json

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise InvalidPolicy(code, f"{path}: {key} appears twice")
            result[key] = value
        return result

    def no_constant(name: str) -> object:
        raise InvalidPolicy(code, f"{path}: {name} is not allowed")

    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique, parse_constant=no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, InvalidPolicy):
            raise
        raise InvalidPolicy(code, f"{path}: not valid JSON") from None


def _plist(data: bytes, path: Path) -> object:
    import plistlib
    from xml.parsers.expat import ExpatError

    try:
        return plistlib.loads(data)
    except (plistlib.InvalidFileException, ExpatError, ValueError, OverflowError, TypeError, KeyError) as exc:
        raise InvalidPolicy("invalid", f"{path}: not a property list ({exc.__class__.__name__})") from None


def _signed_file(system: System, candidate: _Candidate, data: bytes) -> tuple[Any, str | None]:
    path = candidate.path
    assert path is not None
    keys_path = path.parent / KEYS_FILE
    signature_path = path.with_name(path.name + ".sig")
    keys: tuple[tuple[str, str], ...] = ()
    if os.path.lexists(keys_path):
        try:
            keys = parse_keys(_json(_trusted_bytes(system, keys_path, candidate.boundary), keys_path, "keys_invalid"))
        except InvalidPolicy as exc:
            raise InvalidPolicy("keys_invalid" if exc.code == "invalid" else exc.code, exc.detail) from None
    signer = None
    if os.path.lexists(signature_path):
        if not keys:
            raise InvalidPolicy("signature_key_missing", f"{signature_path} is present but {keys_path} is not")
        signer = _verify(data, _trusted_bytes(system, signature_path, candidate.boundary), keys, signature_path)
    elif keys:
        raise InvalidPolicy("signature_missing", f"{keys_path} pins signing keys, but {signature_path} is missing")
    settings, require_signature = parse(_json(data, path))
    if require_signature and signer is None:
        raise InvalidPolicy("signature_missing", f"require_signature is set, but {signature_path} is missing")
    return settings, signer


def _verify(data: bytes, signature_text: bytes, keys: tuple[tuple[str, str], ...], path: Path) -> str:
    import base64
    import binascii

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        signature = base64.b64decode(signature_text.strip(), validate=True)
    except (binascii.Error, ValueError):
        signature = b""
    if len(signature) != 64:
        raise InvalidPolicy("signature_invalid", f"{path}: not a base64 Ed25519 signature")
    for key_id, public in keys:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(signature, data)
        except (InvalidSignature, ValueError):
            continue
        return key_id
    raise InvalidPolicy("signature_invalid", f"{path}: no pinned key signed this policy")


# --- Registry -----------------------------------------------------------------------------------------------

def document_from_registry(tree: RegistryKey) -> dict[str, Any]:
    """The policy document the registry values under ``HKLM\\SOFTWARE\\Policies\\Row-Bot`` spell.

    Root values ``schema_version`` and ``require_signature`` (REG_DWORD); one subkey per section, holding a
    value per setting (REG_DWORD 0 or 1 for true/false and numbers, REG_SZ for text) and a subkey per list
    whose REG_SZ values are the entries. Group Policy leaves empty keys behind, so an empty list key alone is
    "not set"; the ADMX also writes ``<list>_set`` = 1 beside it when the admin sets the list, and that marker
    with no entries is the empty list, as ``[]`` is in JSON.
    """
    where = f"HKLM\\{REGISTRY_KEY}"

    def refuse(path: str, what: str) -> InvalidPolicy:
        return InvalidPolicy("invalid", f"{path}: {what}")

    document: dict[str, Any] = {}
    for name, (kind, data) in tree.values.items():
        if name not in ("schema_version", "require_signature"):
            raise refuse(f"{where}\\{name}", "not a policy value")
        if kind != REG_DWORD or type(data) is not int:
            raise refuse(f"{where}\\{name}", "expected a REG_DWORD")
        document[name] = data if name == "schema_version" else _registry_bool(data, f"{where}\\{name}")
    for section, key in tree.subkeys.items():
        if section not in SECTIONS:
            raise refuse(f"{where}\\{section}", "not a policy section")
        section_specs = specs(section)
        values: dict[str, Any] = {}
        for name, (kind, data) in key.values.items():
            path = f"{where}\\{section}\\{name}"
            listed = section_specs.get(name.removesuffix(LIST_MARKER)) if name.endswith(LIST_MARKER) else None
            if listed is not None and listed.kind == "list":
                if kind != REG_DWORD or data != 1:
                    raise refuse(path, "expected a REG_DWORD 1")
                values[name.removesuffix(LIST_MARKER)] = []
                continue
            spec = section_specs.get(name)
            if spec is None or spec.kind == "list":
                raise refuse(path, "not a policy value here")
            if spec.kind in ("bool", "int"):
                if kind != REG_DWORD or type(data) is not int:
                    raise refuse(path, "expected a REG_DWORD")
                values[name] = _registry_bool(data, path) if spec.kind == "bool" else data
            else:
                if kind != REG_SZ or type(data) is not str:
                    raise refuse(path, "expected a REG_SZ")
                values[name] = data
        for name, items in key.subkeys.items():
            path = f"{where}\\{section}\\{name}"
            spec = section_specs.get(name)
            if spec is None or spec.kind != "list" or items.subkeys:
                raise refuse(path, "not a policy list")
            if not items.values:
                continue
            entries = []
            for value_kind, data in items.values.values():
                if value_kind != REG_SZ or type(data) is not str:
                    raise refuse(path, "list entries must be REG_SZ")
                entries.append(data)
            values[name] = entries
        if values:
            document[section] = values
    return document


def _registry_bool(data: int, path: str) -> bool:
    if data not in (0, 1):
        raise InvalidPolicy("invalid", f"{path}: expected 0 or 1")
    return data == 1


def read_registry(path: str = REGISTRY_KEY, hive: int | None = None) -> RegistryKey | None:
    """The key at *path* under HKLM (or *hive*), or None when it doesn't exist. Windows only."""
    import winreg

    def read(key: Any, depth: int) -> RegistryKey:
        values: dict[str, tuple[int, object]] = {}
        subkeys: dict[str, RegistryKey] = {}
        count_subkeys, count_values, _ = winreg.QueryInfoKey(key)
        if count_values > 4096 or count_subkeys > 64 or depth > 3:
            raise OSError(f"{path}: too many registry values")
        for index in range(count_values):
            name, data, kind = winreg.EnumValue(key, index)
            values[name] = (kind, data)
        for index in range(count_subkeys):
            name = winreg.EnumKey(key, index)
            with winreg.OpenKeyEx(key, name, 0, winreg.KEY_READ) as child:
                subkeys[name] = read(child, depth + 1)
        return RegistryKey(values, subkeys)

    root = winreg.HKEY_LOCAL_MACHINE if hive is None else hive
    try:
        key = winreg.OpenKeyEx(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except FileNotFoundError:
        return None
    with key:
        return read(key, 0)


def _program_data() -> Path:
    """``%ProgramData%`` from the known-folder API, which a person can't redirect as they can the variable."""
    import ctypes
    from ctypes import wintypes

    class Guid(ctypes.Structure):
        _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD), ("data3", wintypes.WORD),
                    ("data4", ctypes.c_ubyte * 8)]

    folder = Guid(0x62AB5D82, 0xFDC1, 0x4DC3, (ctypes.c_ubyte * 8)(0xA9, 0xDD, 0x07, 0x0D, 0x1D, 0x49, 0x5D, 0x97))
    shell = ctypes.WinDLL("shell32")
    known_folder = shell.SHGetKnownFolderPath
    known_folder.argtypes = [ctypes.POINTER(Guid), wintypes.DWORD, wintypes.HANDLE,
                             ctypes.POINTER(ctypes.c_wchar_p)]
    known_folder.restype = ctypes.c_long
    free = ctypes.WinDLL("ole32").CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    pointer = ctypes.c_wchar_p()
    if known_folder(ctypes.byref(folder), 0, None, ctypes.byref(pointer)) != 0:
        raise OSError("ProgramData known folder unavailable")
    try:
        return Path(pointer.value or "")
    finally:
        free(ctypes.cast(pointer, ctypes.c_void_p))
