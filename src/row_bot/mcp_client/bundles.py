"""MCP Bundles (``.mcpb``): read, check and unpack a bundle without running anything in it.

A bundle is a zip with ``manifest.json`` at its root, optionally followed by a detached CMS signature
block. Reading checks the manifest strictly, every archive path, the declared inputs and the signature
from the bytes alone. Unpacking writes regular files only, into a new private folder, counting what it
writes rather than trusting the archive's headers. A signature is checked against the certificate it
carries; whether that certificate is trusted is not decided here.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile
import zlib

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import NameOID

from row_bot.integrations import inputs
from row_bot.package_files import check_package_tree, contained_path, relative_package_path

MAX_BYTES = 128 * 1024 * 1024
MAX_UNPACKED = 512 * 1024 * 1024
MAX_FILES = 20000
_LOG = logging.getLogger(__name__)
_MANIFEST = 1024 * 1024
_START, _END = b"MCPB_SIG_V1", b"MCPB_SIG_END"
_VERSIONS = {"0.1", "0.2", "0.3", "0.4"}
_TYPES = {"node", "python", "binary", "uv"}
_PLATFORMS = {"win32", "darwin", "linux"}
_REQUIRED = {"name": str, "version": str, "description": str, "author": dict, "server": dict}
_OPTIONAL = {"$schema": str, "manifest_version": str, "dxt_version": str, "display_name": str, "long_description": str,
             "repository": dict, "homepage": str, "documentation": str, "support": str, "icon": str, "icons": list,
             "screenshots": list, "keywords": list, "license": str, "user_config": dict, "tools": list,
             "tools_generated": bool, "prompts": list, "prompts_generated": bool, "compatibility": dict,
             "privacy_policies": list, "localization": dict, "_meta": dict}
_FORMATS = {"string": "string", "number": "number", "boolean": "boolean", "directory": "filepath", "file": "filepath"}
_FOLDERS = {"HOME": "", "DESKTOP": "Desktop", "DOCUMENTS": "Documents", "DOWNLOADS": "Downloads"}
_SEPARATORS = {"/", "pathSeparator"}
_VARIABLE = re.compile(r"\$\{([^}]*)\}")
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_DATA, _SIGNED_DATA = "1.2.840.113549.1.7.1", "1.2.840.113549.1.7.2"
_CONTENT_TYPE, _MESSAGE_DIGEST = "1.2.840.113549.1.9.3", "1.2.840.113549.1.9.4"
_HASHES = {"2.16.840.1.101.3.4.2.1": hashes.SHA256, "2.16.840.1.101.3.4.2.2": hashes.SHA384,
           "2.16.840.1.101.3.4.2.3": hashes.SHA512}
_SIGNATURES = {"1.2.840.113549.1.1.1": ("rsa", ""), "1.2.840.113549.1.1.11": ("rsa", "sha256"),
               "1.2.840.113549.1.1.12": ("rsa", "sha384"), "1.2.840.113549.1.1.13": ("rsa", "sha512"),
               "1.2.840.10045.2.1": ("ec", ""), "1.2.840.10045.4.3.2": ("ec", "sha256"),
               "1.2.840.10045.4.3.3": ("ec", "sha384"), "1.2.840.10045.4.3.4": ("ec", "sha512")}
# What a package-tree check refuses while unpacking, said the way a bundle is refused.
_UNPACK = {"package_capacity_exceeded": "bundle_too_large", "unsafe_package_path": "bundle_unsafe_path",
           "package_link_not_allowed": "bundle_unsafe_path", "package_path_collision": "bundle_unsafe_path",
           "package_file_type_invalid": "bundle_unsafe_path"}


@dataclass(frozen=True)
class Bundle:
    """What a checked bundle declares. The launch is in Row-Bot's template syntax: ``{bundle}`` is the
    unpacked folder and ``{key}`` a declared input."""
    name: str
    display_name: str
    version: str
    description: str
    author: str
    server_type: str
    sha256: str
    signature: dict
    inputs: tuple[dict, ...]
    command: str
    args: tuple[str, ...]
    env: dict
    entry_point: str


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def _items(data: bytes, start: int, end: int) -> list[tuple[int, int, int, int]]:
    """The DER elements between two offsets as ``(tag, offset, content start, end)``; definite lengths only."""
    found = []
    while start < end:
        if len(found) >= 4096 or start + 2 > end or data[start] & 0x1F == 0x1F or data[start + 1] == 0x80 or data[start + 1] > 0x84:
            raise ValueError("bundle_signature_invalid")  # Multi-byte tags, indefinite or over-long lengths.
        size, head = data[start + 1], start + 2
        if size & 0x80:
            size, head = int.from_bytes(data[head:head + (size & 0x7F)], "big"), head + (size & 0x7F)
        if head + size > end:
            raise ValueError("bundle_signature_invalid")
        found.append((data[start], start, head, head + size))
        start = head + size
    return found


def _inner(data: bytes, item: tuple[int, int, int, int], tag: int = 0x30) -> list[tuple[int, int, int, int]]:
    """The elements inside one constructed element, which must carry the expected tag."""
    if item[0] != tag:
        raise ValueError("bundle_signature_invalid")
    return _items(data, item[2], item[3])


def _oid(data: bytes, item: tuple[int, int, int, int]) -> str:
    if item[0] != 0x06 or not 0 < item[3] - item[2] <= 64:
        raise ValueError("bundle_signature_invalid")
    numbers, value = [], 0
    for byte in data[item[2]:item[3]]:
        value = value << 7 | byte & 0x7F
        if not byte & 0x80:
            numbers.append(value)
            value = 0
    first = min(numbers[0] // 40, 2)
    return ".".join(map(str, [first, numbers[0] - 40 * first, *numbers[1:]]))


def _issuer_serial(der: bytes) -> tuple[bytes, bytes]:
    """A certificate's issuer name (as encoded) and serial number bytes, to match a signer by."""
    fields = _inner(der, _inner(der, _items(der, 0, len(der))[0])[0])
    fields = fields[1:] if fields[0][0] == 0xA0 else fields
    return der[fields[2][1]:fields[2][3]], der[fields[0][2]:fields[0][3]]


def _verify(signed: bytes, cms: bytes) -> dict:
    """Check a detached CMS signature over the signed zip bytes and say who signed it."""
    try:
        (info,) = _items(cms, 0, len(cms))
        kind, content = _inner(cms, info)
        (body,) = _inner(cms, content, 0xA0)
        fields = _inner(cms, body)
        encapsulated = _inner(cms, fields[2])
        if _oid(cms, kind) != _SIGNED_DATA or len(encapsulated) != 1 or _oid(cms, encapsulated[0]) != _DATA:
            raise ValueError  # Detached: the signed zip is never carried inside the signature.
        certificates = [cms[item[1]:item[3]] for part in fields[3:-1] if part[0] == 0xA0 for item in _inner(cms, part, 0xA0)]
        (signer,) = _inner(cms, fields[-1], 0x31)
        sid, algorithm, attributes, method, signature = _inner(cms, signer)[1:6]
        digest = _HASHES[_oid(cms, _inner(cms, algorithm)[0])]()
        key_kind, named = _SIGNATURES[_oid(cms, _inner(cms, method)[0])]
        found: dict = {}
        for attribute in _inner(cms, attributes, 0xA0):
            name, values = _inner(cms, attribute)
            if _oid(cms, name) in found:
                raise ValueError
            found[_oid(cms, name)] = _inner(cms, values, 0x31)
        (message,), (content_type,) = found[_MESSAGE_DIGEST], found[_CONTENT_TYPE]
        if (named not in ("", digest.name) or _oid(cms, content_type) != _DATA or message[0] != 0x04 or signature[0] != 0x04
                or cms[message[2]:message[3]] != hashlib.new(digest.name, signed).digest()):
            raise ValueError
        if sid[0] == 0x30:
            issuer, serial = _inner(cms, sid)
            wanted = (cms[issuer[1]:issuer[3]], cms[serial[2]:serial[3]])
            matches = [der for der in certificates if _issuer_serial(der) == wanted]
        else:
            matches = certificates if len(certificates) == 1 else []
        certificate = x509.load_der_x509_certificate(matches[0])
        key, payload = certificate.public_key(), b"\x31" + cms[attributes[1] + 1:attributes[3]]  # Signed as a SET.
        if key_kind == "rsa" and isinstance(key, rsa.RSAPublicKey):
            key.verify(cms[signature[2]:signature[3]], payload, padding.PKCS1v15(), digest)
        elif key_kind == "ec" and isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(cms[signature[2]:signature[3]], payload, ec.ECDSA(digest))
        else:
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError, InvalidSignature, UnsupportedAlgorithm):
        raise ValueError("bundle_signature_invalid") from None
    names = (certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
             or certificate.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME))
    return {"status": "signed", "signer": str(names[0].value)[:256] if names else certificate.subject.rfc4514_string()[:256],
            "fingerprint": certificate.fingerprint(hashes.SHA256()).hex()}


def _signature(data: bytes) -> tuple[bytes, dict]:
    """The zip as it was signed and who signed it. A damaged or non-matching signature block is
    refused, never read as unsigned: a tampered bundle must not pass for an unsigned one."""
    if not data.endswith(_END):
        eocd = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
        if eocd >= 0 and eocd + 22 + _u16(data, eocd + 20) != len(data) and _START in data[eocd + 22:]:
            raise ValueError("bundle_signature_invalid")  # A signature block cut short.
        return data, {"status": "unsigned"}
    start = data.rfind(_START, 0, len(data) - len(_END))
    size = int.from_bytes(data[start + 11:start + 15], "little") if start >= 0 else -1
    if start < 0 or start + 15 + size + len(_END) != len(data):
        raise ValueError("bundle_signature_invalid")
    zipped = bytearray(data[:start])
    eocd = zipped.rfind(b"PK\x05\x06", max(0, len(zipped) - 65557))
    if eocd >= 0 and eocd + 22 + _u16(zipped, eocd + 20) == len(data):  # The signer grew the comment over its block.
        zipped[eocd + 20:eocd + 22] = (_u16(zipped, eocd + 20) - (len(data) - start)).to_bytes(2, "little")
    return bytes(zipped), _verify(bytes(zipped), data[start + 15:start + 15 + size])


def _members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Regular files by their checked path. Links, special files, escapes, drive names and
    case-insensitive collisions are refused; sizes here are only what the headers claim."""
    if len(archive.infolist()) > MAX_FILES:
        raise ValueError("bundle_too_large")
    files: dict[str, zipfile.ZipInfo] = {}
    seen, folders, total = set(), set(), 0
    for info in archive.infolist():
        name, kind = info.orig_filename, (info.external_attr >> 16) & 0o170000  # As stored, before any clean-up.
        try:
            path = relative_package_path(name[:-1] if info.is_dir() else name)
        except ValueError:
            raise ValueError("bundle_unsafe_path") from None
        if kind not in (0, 0o040000 if info.is_dir() else 0o100000):
            raise ValueError("bundle_unsafe_path")
        parts = path.casefold().split("/")
        folders.update("/".join(parts[:index]) for index in range(1, len(parts) + info.is_dir()))
        if info.is_dir():
            continue
        if info.flag_bits & 0x1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError("bundle_invalid")
        if path.casefold() in seen:
            raise ValueError("bundle_unsafe_path")
        seen.add(path.casefold())
        files[path], total = info, total + info.file_size
    if len(files) + len(folders) > MAX_FILES or total > MAX_UNPACKED:
        raise ValueError("bundle_too_large")
    if seen & folders:
        raise ValueError("bundle_unsafe_path")
    return files


def _template(text: str, found: dict, carrier: str | None) -> str:
    """Bundle variables in Row-Bot's template syntax. ``carrier`` is the environment variable the text
    fills, ``""`` for an argument and None for the command; ``found`` records where each input goes."""
    if re.search(r"\{[A-Za-z_][A-Za-z0-9_]{0,63}\}", _VARIABLE.sub("", text)):
        raise ValueError("bundle_invalid")  # A literal placeholder would be filled with a person's value.

    def swap(match: re.Match) -> str:
        if match[1].startswith("user_config.") and inputs.KEY.fullmatch(match[1][12:]):
            if carrier is None:
                raise ValueError("bundle_inputs_unsupported")  # A person's value never picks what runs.
            found.setdefault(match[1][12:], carrier)
            return "{" + match[1][12:] + "}"
        if match[1] != "__dirname" and match[1] not in _SEPARATORS:
            raise ValueError("bundle_invalid")
        return "{bundle}" if match[1] == "__dirname" else "/"
    return _VARIABLE.sub(swap, text)


def _bundled(name: str, value: str) -> bool:
    """A library path into the bundle itself (vendored Python or Node libraries): the only variable
    that changes what runs which a bundle may still set."""
    return name.upper() in {"PYTHONPATH", "NODE_PATH"} and all(
        part == "{bundle}" or (part.startswith("{bundle}/") and "{" not in part[8:] and ".." not in part.split("/"))
        for part in re.split(r"[;:]", value))


def _launch(config: dict, platform: str) -> tuple[str, list[str], dict, dict]:
    """The command, arguments and environment for this platform as templates, and where each input goes."""
    overrides = config.get("platform_overrides", {})
    override = overrides.get(platform, {}) if type(overrides) is dict else None
    if type(override) is not dict or type(config.get("env", {})) is not dict or type(override.get("env", {})) is not dict:
        raise ValueError("bundle_invalid")
    command, args = override.get("command", config.get("command")), override.get("args", config.get("args", []))
    env = {**config.get("env", {}), **override.get("env", {})}
    if (type(command) is not str or not command or type(args) is not list or len(args) > 256
            or not all(type(value) is str and len(value) <= 4096 for value in [*args, *env, *env.values()])):
        raise ValueError("bundle_invalid")
    found: dict = {}
    env = {name: _template(value, found, name) for name, value in env.items()}  # First: an input used here is an env input.
    if not all(_ENV.fullmatch(name) and (name.upper() not in inputs.NEVER_ENV or _bundled(name, value))
               for name, value in env.items()):
        raise ValueError("bundle_inputs_unsupported")
    return _template(command, found, None), [_template(value, found, "") for value in args], env, found


def _default(value: object) -> str:
    """A user_config default as a value, with ``${HOME}``, ``${DESKTOP}``, ``${DOCUMENTS}`` and
    ``${DOWNLOADS}`` made this computer's folders."""
    if type(value) is bool:
        return "true" if value else "false"
    if value is None or type(value) in (int, float):
        return "" if value is None else str(value)
    if type(value) is not str or any(name not in {*_FOLDERS, *_SEPARATORS} for name in _VARIABLE.findall(value)):
        raise ValueError("bundle_inputs_unsupported")
    return _VARIABLE.sub(lambda match: os.sep if match[1] in _SEPARATORS else str(Path.home() / _FOLDERS[match[1]]), value)


def _inputs(user_config: dict, found: dict) -> tuple[dict, ...]:
    """Each user_config entry as a declared input: an environment variable where the bundle puts
    it in one, an argument otherwise."""
    declared = []
    for key, spec in user_config.items():
        if not inputs.KEY.fullmatch(key) or type(spec) is not dict or spec.get("multiple"):
            raise ValueError("bundle_inputs_unsupported")
        if (spec.get("type") not in _FORMATS or type(spec.get("title")) is not str or type(spec.get("description")) is not str
                or any(type(spec.get(flag, False)) is not bool for flag in ("required", "sensitive", "multiple"))):
            raise ValueError("bundle_invalid")
        declared.append(inputs.declaration(key, target="env" if found.get(key) else "argument", name=found.get(key) or key,
            label=spec["title"], description=spec["description"], secret=spec.get("sensitive", False),
            required=spec.get("required", False), default=_default(spec.get("default")), format=_FORMATS[spec["type"]]))
    if any(key not in user_config for key in found):
        raise ValueError("bundle_invalid")
    try:
        return tuple(inputs.check(declared))
    except inputs.InputError:
        raise ValueError("bundle_inputs_unsupported") from None


def _read(data: bytes, platform: str | None) -> tuple[Bundle, bytes]:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("bundle_too_large" if data else "bundle_invalid")
    zipped, signature = _signature(bytes(data))
    try:
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            files = _members(archive)
            if "manifest.json" not in files or files["manifest.json"].file_size > _MANIFEST:
                raise ValueError("bundle_invalid")
            manifest = json.loads(archive.read(files["manifest.json"]).decode("utf-8-sig"))
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, OSError, UnicodeDecodeError,
            json.JSONDecodeError, RecursionError):
        raise ValueError("bundle_invalid") from None
    if type(manifest) is not dict or not ("manifest_version" in manifest or "dxt_version" in manifest):
        raise ValueError("bundle_invalid")
    if manifest.get("manifest_version", manifest.get("dxt_version")) not in _VERSIONS:
        raise ValueError("bundle_unsupported_version")
    known = {**_REQUIRED, **_OPTIONAL}
    if not _REQUIRED.keys() <= manifest.keys() or any(type(value) is not known.get(key) for key, value in manifest.items()):
        raise ValueError("bundle_invalid")  # Unknown fields too: what Row-Bot cannot read, it does not accept.
    name, version, server, author = manifest["name"], manifest["version"], manifest["server"], manifest["author"]
    compatibility = manifest.get("compatibility", {})
    platforms, runtimes = compatibility.get("platforms", sorted(_PLATFORMS)), compatibility.get("runtimes", {})
    if (not 0 < len(name) <= 128 or not 0 < len(version) <= 64 or type(author.get("name")) is not str
            or type(server.get("type")) is not str or type(server.get("entry_point")) is not str
            or type(server.get("mcp_config")) is not dict or type(platforms) is not list or type(runtimes) is not dict
            or not all(type(value) is str for value in [*platforms, *runtimes, *runtimes.values()])):
        raise ValueError("bundle_invalid")
    if server["type"] not in _TYPES or (server["type"] == "uv" and manifest.get("manifest_version") != "0.4"):
        raise ValueError("bundle_runtime_unsupported")
    platform = platform or {"win32": "win32", "darwin": "darwin"}.get(sys.platform, "linux")
    if platform not in _PLATFORMS or platform not in platforms:
        raise ValueError("bundle_platform_unsupported")
    try:
        entry = relative_package_path(server["entry_point"].removeprefix("./"))
    except ValueError:
        raise ValueError("bundle_unsafe_path") from None
    if entry not in files:
        raise ValueError("bundle_entry_missing")
    command, args, env, found = _launch(server["mcp_config"], platform)
    bundle = Bundle(name=name, display_name=(manifest.get("display_name") or name)[:128], version=version,
        description=manifest["description"][:2048], author=author["name"][:256],
        server_type=server["type"], sha256=hashlib.sha256(data).hexdigest(), signature=signature,
        inputs=_inputs(manifest.get("user_config", {}), found), command=command, args=tuple(args), env=env,
        entry_point=entry)
    _LOG.info("Checked MCP bundle %s %s (%s)", name, version, signature["status"])
    return bundle, zipped


def read(data: bytes, *, platform: str | None = None) -> Bundle:
    """Check a bundle from its bytes alone; nothing is extracted or run. ``platform`` is ``win32``,
    ``darwin`` or ``linux``, this computer's by default. Refusals are ``ValueError`` codes:
    ``bundle_too_large``, ``bundle_invalid``, ``bundle_unsupported_version``, ``bundle_unsafe_path``,
    ``bundle_platform_unsupported``, ``bundle_runtime_unsupported``, ``bundle_inputs_unsupported``,
    ``bundle_signature_invalid`` and ``bundle_entry_missing``."""
    return _read(data, platform)[0]


def extract(data: bytes, destination: Path, *, check: Callable[[], None] = lambda: None) -> str:
    """Unpack a bundle into a new private folder that must not exist yet and return its tree digest.
    ``check`` runs every 256 files or 8 MiB written, to stop early; on any failure the folder is removed."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("bundle_destination_exists")
    bundle, zipped = _read(data, None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError("bundle_destination_exists") from None
    runnable = set()
    if bundle.server_type == "binary" and os.name != "nt":  # Zips often drop the mode; the declared binary must run.
        runnable = {bundle.entry_point, *([bundle.command[9:]] if bundle.command.startswith("{bundle}/") else [])}
    try:
        written = mark = 0
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            for index, (name, info) in enumerate(_members(archive).items()):
                if index % 256 == 0:
                    check()
                target = contained_path(destination, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "xb") as output:
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > MAX_UNPACKED:  # Count what arrives; a header can understate it.
                            raise ValueError("bundle_too_large")
                        if written - mark >= 8 * 1024 * 1024:
                            check()
                            mark = written
                        output.write(chunk)
                if name in runnable:
                    target.chmod(0o755)
        check_package_tree(destination, max_files=MAX_FILES, max_bytes=MAX_UNPACKED)
        from row_bot.plugins.devtools import compute_plugin_checksum
        return compute_plugin_checksum(destination)
    except BaseException as error:
        shutil.rmtree(destination, ignore_errors=True)
        _LOG.info("MCP bundle %s was not unpacked: %s", bundle.name, type(error).__name__)
        if isinstance(error, (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError)):
            raise ValueError("bundle_invalid") from None
        if type(error) is ValueError and str(error) in _UNPACK:
            raise ValueError(_UNPACK[str(error)]) from None
        raise
