"""MCP bundles (.mcpb) are read and unpacked from synthetic archives; nothing in them runs."""
import copy
import datetime
import hashlib
import io
import json
import os
import subprocess
import zipfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID
import pytest

from row_bot.mcp_client import bundles
from row_bot.plugins.devtools import compute_plugin_checksum

pytestmark = pytest.mark.platform

MANIFEST = {
    "manifest_version": "0.3", "name": "fixture-notes", "version": "1.2.0", "description": "Reads notes.",
    "author": {"name": "Fixture Author"}, "license": "MIT",
    "server": {"type": "node", "entry_point": "server/index.js", "mcp_config": {
        "command": "node", "args": ["${__dirname}${/}server/index.js", "--root", "${user_config.folder}"],
        "env": {"NOTES_TOKEN": "${user_config.api_key}", "LOG_LEVEL": "info", "NODE_PATH": "${__dirname}/server/lib"}}},
    "user_config": {
        "api_key": {"type": "string", "title": "API key", "description": "Your key.", "sensitive": True, "required": True},
        "folder": {"type": "directory", "title": "Notes folder", "description": "Where notes live.",
                   "default": "${DOCUMENTS}${/}notes"}},
}


def manifest(change=None):
    value = copy.deepcopy(MANIFEST)
    if change:
        change(value)
    return value


def archive(value=None, files=None, *, extra=()):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("manifest.json", json.dumps(manifest() if value is None else value))
        for name, data in (files or {"server/index.js": "// fixture server"}).items():
            output.writestr(name, data)
        for info, data in extra:
            output.writestr(info, data)
    return stream.getvalue()


def entry(name, *, mode=0):
    info = zipfile.ZipInfo("placeholder")
    info.filename, info.external_attr, info.create_system = name, mode << 16, 3 if mode else 0
    return info


@pytest.fixture(scope="module")
def signers():
    found = {}
    when = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    for kind, key in (("ec", ec.generate_private_key(ec.SECP256R1())),
                      ("rsa", rsa.generate_private_key(public_exponent=65537, key_size=2048))):
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fixture Publisher")])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(4242).not_valid_before(when).not_valid_after(when + datetime.timedelta(days=365))
                .sign(key, hashes.SHA256()))
        found[kind] = key, cert
    return found


def sign(zipped, signer, *, patch=True):
    key, cert = signer
    der = pkcs7.PKCS7SignatureBuilder().set_data(zipped).add_signer(cert, key, hashes.SHA256()).sign(
        serialization.Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary])
    block = b"MCPB_SIG_V1" + len(der).to_bytes(4, "little") + der + b"MCPB_SIG_END"
    if patch:  # As the signer does: the zip comment grows over the block so zip readers accept the file.
        eocd = zipped.rfind(b"PK\x05\x06")
        size = int.from_bytes(zipped[eocd + 20:eocd + 22], "little") + len(block)
        zipped = zipped[:eocd + 20] + size.to_bytes(2, "little") + zipped[eocd + 22:]
    return zipped + block


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def test_node_bundle_maps_user_config_to_declared_inputs_and_templates(home):
    data = archive()
    bundle = bundles.read(data, platform="linux")
    assert (bundle.name, bundle.display_name, bundle.version, bundle.author, bundle.server_type) == (
        "fixture-notes", "fixture-notes", "1.2.0", "Fixture Author", "node")
    assert bundle.sha256 == hashlib.sha256(data).hexdigest() and bundle.signature == {"status": "unsigned"}
    assert bundle.command == "node" and bundle.entry_point == "server/index.js"
    assert bundle.args == ("{bundle}/server/index.js", "--root", "{folder}")
    assert bundle.env == {"NOTES_TOKEN": "{api_key}", "LOG_LEVEL": "info", "NODE_PATH": "{bundle}/server/lib"}
    key, folder = bundle.inputs
    assert (key["key"], key["target"], key["name"], key["secret"], key["required"], key["default"], key["format"]) == (
        "api_key", "env", "NOTES_TOKEN", True, True, "", "string")
    assert (folder["target"], folder["name"], folder["secret"], folder["format"], folder["label"]) == (
        "argument", "folder", False, "filepath", "Notes folder")
    assert folder["default"] == str(home / "Documents") + os.sep + "notes"


def test_a_key_stays_a_secret_whatever_the_bundle_calls_it_and_never_goes_on_a_command_line(home):
    def plain(value):
        value["user_config"]["api_key"].update(sensitive=False, default="fixture-not-a-key")
        value["user_config"]["max_tokens"] = {"type": "number", "title": "Most tokens", "description": "D.", "default": 512}
        value["server"]["mcp_config"]["args"] += ["--max", "${user_config.max_tokens}"]
    key, _folder, limit = bundles.read(archive(manifest(plain)), platform="linux").inputs
    # Kept in the keychain, never saved as a plain setting or shown back as a default.
    assert (key["key"], key["secret"], key["default"]) == ("api_key", True, "")
    assert (limit["key"], limit["secret"], limit["default"]) == ("max_tokens", False, "512")  # A number, not a key.

    def on_argv(value):
        value["server"]["mcp_config"]["env"].pop("NOTES_TOKEN")
        value["server"]["mcp_config"]["args"] += ["--key", "${user_config.api_key}"]
    with pytest.raises(ValueError, match="^bundle_inputs_unsupported$"):
        bundles.read(archive(manifest(on_argv)), platform="linux")


def test_platform_override_and_declared_platforms(home):
    def override(value):
        value["server"]["mcp_config"]["platform_overrides"] = {
            "win32": {"command": "node.exe", "env": {"LOG_LEVEL": "debug", "EXTRA": "1"}}}
        value["compatibility"] = {"platforms": ["win32", "linux"], "runtimes": {"node": ">=18"}}
    data = archive(manifest(override))
    windows, linux = bundles.read(data, platform="win32"), bundles.read(data, platform="linux")
    assert windows.command == "node.exe" and windows.args == linux.args
    assert windows.env == {**linux.env, "LOG_LEVEL": "debug", "EXTRA": "1"}
    assert linux.command == "node" and linux.env["LOG_LEVEL"] == "info"
    with pytest.raises(ValueError, match="^bundle_platform_unsupported$"):
        bundles.read(data, platform="darwin")


@pytest.mark.parametrize(("change", "code"), [
    (lambda m: m["server"]["mcp_config"]["args"].append("${FOO}"), "bundle_invalid"),
    (lambda m: m["server"]["mcp_config"]["args"].append("${HOME}/x"), "bundle_invalid"),
    (lambda m: m["server"]["mcp_config"]["args"].append("${user_config.missing}"), "bundle_invalid"),
    (lambda m: m["server"]["mcp_config"]["args"].append("{api_key}"), "bundle_invalid"),
    (lambda m: m["server"]["mcp_config"].update(command="${user_config.folder}"), "bundle_inputs_unsupported"),
    (lambda m: m["server"]["mcp_config"]["env"].update(NODE_OPTIONS="--require x"), "bundle_inputs_unsupported"),
    (lambda m: m["server"]["mcp_config"]["env"].update(NODE_PATH="${__dirname}/../shared"), "bundle_inputs_unsupported"),
    (lambda m: m["server"]["mcp_config"]["env"].update(PYTHONPATH="/opt/lib"), "bundle_inputs_unsupported"),
    (lambda m: m["user_config"]["folder"].update(multiple=True), "bundle_inputs_unsupported"),
    (lambda m: m["user_config"].update({"bad-key": {"type": "string", "title": "T", "description": "D"}}),
     "bundle_inputs_unsupported"),
    (lambda m: m["user_config"]["folder"].update(default="${SECRETS}"), "bundle_inputs_unsupported"),
])
def test_launch_variables_and_inputs_are_refused_unless_supported(home, change, code):
    with pytest.raises(ValueError, match=f"^{code}$"):
        bundles.read(archive(manifest(change)), platform="linux")


@pytest.mark.parametrize(("value", "files", "code"), [
    (manifest(lambda m: m.update(telemetry=True)), None, "bundle_invalid"),
    (manifest(lambda m: m.pop("description")), None, "bundle_invalid"),
    (manifest(lambda m: m["author"].pop("name")), None, "bundle_invalid"),
    (manifest(lambda m: m.update(manifest_version="0.9")), None, "bundle_unsupported_version"),
    (manifest(lambda m: m["server"].update(type="java")), None, "bundle_runtime_unsupported"),
    (manifest(lambda m: m["server"].update(type="uv")), None, "bundle_runtime_unsupported"),
    (manifest(lambda m: m["server"].update(entry_point="server/main.js")), None, "bundle_entry_missing"),
    (manifest(lambda m: m["server"].update(entry_point="../index.js")), None, "bundle_unsafe_path"),
    (manifest(), {"server/index.js/": ""}, "bundle_entry_missing"),
])
def test_manifest_is_checked_strictly(home, value, files, code):
    with pytest.raises(ValueError, match=f"^{code}$"):
        bundles.read(archive(value, files), platform="linux")


def test_legacy_manifest_and_damaged_archives(home):
    legacy = manifest(lambda m: m.update(dxt_version=m.pop("manifest_version")))
    assert bundles.read(archive(legacy), platform="linux").name == "fixture-notes"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        output.writestr("README.md", "no manifest")
    for data in (b"", b"not a zip", archive()[:-30], stream.getvalue()):
        with pytest.raises(ValueError, match="^bundle_invalid$"):
            bundles.read(data, platform="linux")


@pytest.mark.parametrize("extra", [
    [(entry("../escape.js"), "x")],
    [(entry("/absolute.js"), "x")],
    [(entry("C:/drive.js"), "x")],
    [(entry("server\\evil.js"), "x")],
    [(entry("server/nul\0.js"), "x")],
    [(entry("server/link.js", mode=0o120777), "/etc/passwd")],
    [(entry("server/fifo", mode=0o010644), "")],
    [(entry("SERVER/INDEX.JS"), "x")],
    [(entry("server/index.js/inner.js"), "x")],
])
def test_unsafe_archive_entries_are_refused(home, extra):
    with pytest.raises(ValueError, match="^bundle_unsafe_path$"):
        bundles.read(archive(extra=extra), platform="linux")


def test_size_and_count_caps(home, monkeypatch):
    data = archive(files={"server/index.js": "x" * 4096, "a/b.js": "1", "a/c.js": "2"})
    assert bundles.read(data, platform="linux")
    for name, value in (("MAX_BYTES", len(data) - 1), ("MAX_UNPACKED", 4096), ("MAX_FILES", 5)):
        with monkeypatch.context() as patch:
            patch.setattr(bundles, name, value)
            with pytest.raises(ValueError, match="^bundle_too_large$"):
                bundles.read(data, platform="linux")


@pytest.mark.parametrize(("kind", "patch"), [("rsa", True), ("ec", True), ("ec", False)])
def test_signed_bundle_reports_its_signer(home, signers, kind, patch):
    data = sign(archive(), signers[kind], patch=patch)
    bundle = bundles.read(data, platform="linux")
    assert bundle.signature == {"status": "signed", "signer": "Fixture Publisher",
                                "fingerprint": signers[kind][1].fingerprint(hashes.SHA256()).hex()}
    assert bundle.sha256 == hashlib.sha256(data).hexdigest() and bundle.args[0] == "{bundle}/server/index.js"


def test_tampered_or_damaged_signatures_are_refused_not_read_as_unsigned(home, signers):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as output:
        output.writestr("manifest.json", json.dumps(manifest()))
        output.writestr("server/index.js", "// original server")
    data = sign(stream.getvalue(), signers["ec"])
    assert bundles.read(data, platform="linux").signature["status"] == "signed"
    footer, header = len(b"MCPB_SIG_END"), data.rfind(b"MCPB_SIG_V1")
    damaged = [
        data.replace(b"original", b"tampered"),  # Content changed after signing.
        data[:-footer - 20] + data[-footer:],  # Signature cut short, footer kept.
        data[:-5],  # Footer cut off.
        data[:header + 15] + bytes([data[header + 15] ^ 0xFF]) + data[header + 16:],  # Garbled structure.
        data[:-footer - 3] + bytes([data[-footer - 3] ^ 0x01]) + data[-footer - 2:],  # Garbled signature value.
        data[:header + 11] + (1 << 20).to_bytes(4, "little") + data[header + 15:],  # Wrong length.
    ]
    for value in damaged:
        with pytest.raises(ValueError, match="^bundle_signature_invalid$"):
            bundles.read(value, platform="linux")


def test_extract_writes_only_contained_regular_files(home, tmp_path, signers):
    value = manifest(lambda m: m["server"].update(type="binary", entry_point="bin/server",
                                                  mcp_config={"command": "${__dirname}/bin/server", "args": []}))
    value.pop("user_config")
    data = sign(archive(value, {"bin/server": b"\x7fELF fixture", "lib/data.json": "{}"},
                       extra=[(entry("lib/"), "")]), signers["ec"])
    calls = []
    destination = tmp_path / "bundles" / "fixture"
    digest = bundles.extract(data, destination, check=lambda: calls.append(1))
    assert digest == compute_plugin_checksum(destination) and calls
    assert sorted(path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()) == [
        "bin/server", "lib/data.json", "manifest.json"]
    assert (destination / "bin" / "server").read_bytes() == b"\x7fELF fixture"
    if os.name != "nt":
        assert (destination / "bin" / "server").stat().st_mode & 0o100
        assert not (destination / "lib" / "data.json").stat().st_mode & 0o111
    with pytest.raises(ValueError, match="^bundle_destination_exists$"):
        bundles.extract(data, destination)
    assert compute_plugin_checksum(destination) == digest  # The existing folder is left as it was.
    with pytest.raises(ValueError, match="^bundle_unsafe_path$"):
        bundles.extract(archive(extra=[(entry("../escape.js"), "x")]), tmp_path / "escape")
    assert not (tmp_path / "escape").exists() and not (tmp_path / "escape.js").exists()


def test_extract_removes_what_it_wrote_on_failure(home, tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as output:
        output.writestr("manifest.json", json.dumps(manifest()))
        output.writestr("server/index.js", "// server")
        output.writestr("z/late.txt", "payload-original")
    corrupted = stream.getvalue().replace(b"payload-original", b"payload-tampered")  # Fails its CRC when read.
    assert bundles.read(corrupted, platform="linux")
    with pytest.raises(ValueError, match="^bundle_invalid$"):
        bundles.extract(corrupted, tmp_path / "corrupted")
    assert not (tmp_path / "corrupted").exists()

    def stop():
        raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError, match="cancelled"):
        bundles.extract(stream.getvalue(), tmp_path / "cancelled", check=stop)
    assert not (tmp_path / "cancelled").exists()


def test_reading_and_unpacking_never_run_anything(home, tmp_path, monkeypatch, signers):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a bundle must never be run while it is read or unpacked")
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, refuse)
    monkeypatch.setattr(os, "system", refuse)
    for attribute in ("startfile", "execv", "execve", "spawnv"):
        if hasattr(os, attribute):
            monkeypatch.setattr(os, attribute, refuse)
    data = sign(archive(), signers["rsa"])
    assert bundles.read(data).signature["status"] == "signed"
    assert bundles.extract(data, tmp_path / "unpacked").startswith("sha256:")
