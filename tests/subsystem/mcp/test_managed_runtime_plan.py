"""Pinned runtime installation uses only synthetic archives and fake downloads."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import io
import os
from pathlib import Path
import tarfile
import zipfile

import pytest

from row_bot.mcp_client import requirements as runtime

pytestmark = pytest.mark.subsystem


def archive(members):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as stream:
        for name, data in members.items():
            stream.writestr(name, data)
    return output.getvalue()


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "RUNTIMES_DIR", tmp_path / "runtimes")
    calls = []
    data = archive({"bundle/runtime.exe": b"synthetic executable", "bundle/LICENSE": b"license"})
    def download(url, destination, progress=None):
        calls.append(url)
        destination.write_bytes(data)
    monkeypatch.setattr(runtime, "_download", download)
    def plan(**kwargs):
        return runtime.make_archive_runtime_plan("synthetic", version="1.2.3", url="https://example.invalid/runtime.zip",
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), asset_name="runtime.zip",
            executable_candidates=("runtime.exe",), **kwargs)
    return tmp_path, data, calls, plan


def test_plan_is_passive_immutable_and_install_never_resolves_again(owner, monkeypatch):
    tmp, _, calls, make = owner
    plan = make()
    assert not (tmp / "runtimes").exists() and not calls
    with pytest.raises(FrozenInstanceError):
        plan.version = "changed"
    monkeypatch.setattr(runtime, "_latest_node_lts_version", lambda: pytest.fail("hidden latest resolution"))
    monkeypatch.setattr(runtime, "_latest_uv_asset", lambda: pytest.fail("hidden latest resolution"))
    assert runtime.install_runtime_plan(plan).ok
    assert calls == [plan.url]
    saved = runtime._read_manifest("synthetic")
    assert saved["archive_size"] == plan.size_bytes and saved["archive_sha256"] == plan.sha256
    assert Path(saved["executable_path"]).read_bytes() == b"synthetic executable"
    assert runtime._managed_bin_dir("synthetic") == Path(saved["bin_dir"])


@pytest.mark.parametrize("field,value", [("version", "latest"), ("version", "../escape"),
    ("url", "http://example.invalid/runtime.zip"), ("url", "https://user:secret@example.invalid/a"),
    ("sha256", ""), ("size_bytes", True), ("size_bytes", 0), ("size_bytes", runtime.ARCHIVE_BYTE_LIMIT + 1),
    ("asset_name", "../outside.zip"), ("executable_candidates", ("../../outside.exe",)),
    ("system", "another-os"), ("internal_links", True), ("schema_version", True), ("internal_links", 1)])
def test_forged_or_invalid_plan_rejected_before_download_or_directory_creation(owner, field, value):
    tmp, _, calls, make = owner
    with pytest.raises((RuntimeError, ValueError)):
        runtime.install_runtime_plan(replace(make(), **{field: value}))
    assert not calls and not (tmp / "runtimes").exists()


def test_stale_manifest_revision_rejects_before_downloading(owner):
    _, _, calls, make = owner
    plan = make()
    runtime._write_manifest("synthetic", {"unknown": "preserve current state"})
    with pytest.raises(RuntimeError, match="stale"):
        runtime.install_runtime_plan(plan)
    assert not calls and runtime._read_manifest("synthetic")["unknown"] == "preserve current state"


def test_same_byte_manifest_replacement_invalidates_the_reviewed_plan(owner):
    _, _, calls, make = owner
    runtime._write_manifest("synthetic", {"unknown": "retained"})
    plan = make()
    path = runtime.RUNTIMES_DIR / "synthetic/manifest.json"
    data, before = path.read_bytes(), path.stat()
    path.rename(path.with_name("retained-original.json"))
    path.write_bytes(data)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(RuntimeError, match="stale"):
        runtime.install_runtime_plan(plan)
    assert not calls and path.read_bytes() == data


def test_modified_generation_is_unavailable_and_is_not_adopted_as_already_installed(owner):
    _, _, _, make = owner
    assert runtime.install_runtime_plan(make()).ok
    manifest = runtime._read_manifest("synthetic")
    target = Path(manifest["executable_path"])
    before = target.stat()
    target.write_bytes(b"tampered executable!")
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert runtime._managed_bin_dir("synthetic") is None
    with pytest.raises(RuntimeError, match="integrity"):
        runtime.install_runtime_plan(make())
    assert target.read_bytes() == b"tampered executable!"


@pytest.mark.parametrize("damage", ["hash", "size"])
def test_download_mismatch_retains_prior_manifest_and_generation(owner, monkeypatch, damage):
    _, data, _, make = owner
    runtime.install_runtime_plan(make())
    previous = runtime._manifest_bytes("synthetic")
    plan = replace(make(), version="2.0.0")
    bad = bytes([data[0] ^ 1]) + data[1:] if damage == "hash" else data + b"extra"
    monkeypatch.setattr(runtime, "_download", lambda _u, p, _s=None: p.write_bytes(bad))
    with pytest.raises(RuntimeError, match="mismatch"):
        runtime.install_runtime_plan(plan)
    assert runtime._manifest_bytes("synthetic") == previous
    assert (runtime.RUNTIMES_DIR / "synthetic/1.2.3/runtime.exe").read_bytes() == b"synthetic executable"
    assert not (runtime.RUNTIMES_DIR / "synthetic/2.0.0").exists()


def test_publication_failure_retains_previous_and_unadvertised_new_generation(owner, monkeypatch):
    _, _, _, make = owner
    runtime.install_runtime_plan(make())
    previous = runtime._manifest_bytes("synthetic")
    plan = replace(make(), version="2.0.0")
    monkeypatch.setattr(runtime, "_write_manifest", lambda *_a, **_k: (_ for _ in ()).throw(OSError("publication fault")))
    with pytest.raises(OSError, match="publication fault"):
        runtime.install_runtime_plan(plan)
    assert runtime._manifest_bytes("synthetic") == previous
    assert (runtime.RUNTIMES_DIR / "synthetic/1.2.3/runtime.exe").read_bytes() == b"synthetic executable"
    assert (runtime.RUNTIMES_DIR / "synthetic/2.0.0/runtime.exe").read_bytes() == b"synthetic executable"
    assert runtime._managed_bin_dir("synthetic").name == "1.2.3"


@pytest.mark.parametrize("members", [{"../outside": b"x"}, {"C:/outside": b"x"},
    {"folder/file.txt": b"x", "Folder/other.txt": b"y"}, {"file": b"x", "file/child": b"y"},
    {"NUL.txt": b"x"}, {"file.txt:secret": b"x"}])
def test_complete_archive_paths_are_rejected_before_any_member_is_extracted(tmp_path, members):
    source = tmp_path / "input.zip"
    source.write_bytes(archive(members))
    destination = tmp_path / "output"
    with pytest.raises(RuntimeError):
        runtime._extract_archive(source, destination)
    assert not list(destination.iterdir())
    assert not (tmp_path / "outside").exists()


def node_tar(link="../lib/node_modules/npm/bin/npm-cli.js", *, pivot=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as handle:
        for name in ("node-v1/bin/node", "node-v1/lib/node_modules/npm/bin/npm-cli.js", "node-v1/lib/node_modules/npm/bin/npx-cli.js"):
            info = tarfile.TarInfo(name)
            info.size, info.mode = 6, 0o755
            handle.addfile(info, io.BytesIO(b"script"))
        for name, target in (("npm", link), ("npx", "../lib/node_modules/npm/bin/npx-cli.js")):
            info = tarfile.TarInfo("node-v1/bin/" + name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            handle.addfile(info)
        if pivot:
            info = tarfile.TarInfo("node-v1/bin/npm/child")
            info.size = 1
            handle.addfile(info, io.BytesIO(b"x"))
    return output.getvalue()


@pytest.mark.parametrize("link,pivot", [("../../../outside", False), ("npx", False),
    ("npm", False), ("/absolute", False), ("../lib/node_modules/npm/bin/npm-cli.js", True)])
def test_node_link_graph_rejects_escape_cycles_chains_and_ancestor_pivots(link, pivot):
    with tarfile.open(fileobj=io.BytesIO(node_tar(link, pivot=pivot))) as handle:
        with pytest.raises(RuntimeError):
            runtime._archive_entries(handle, False, internal_links=True)


def test_canonical_node_link_graph_is_valid_but_generic_archive_still_rejects_links(tmp_path):
    data = node_tar()
    with tarfile.open(fileobj=io.BytesIO(data)) as handle:
        entries, links = runtime._archive_entries(handle, False, internal_links=True)
    assert len(entries) == 5 and len(links) == 2
    source = tmp_path / "node.tar.gz"
    source.write_bytes(data)
    with pytest.raises(RuntimeError, match="special entry"):
        runtime._extract_archive(source, tmp_path / "output")


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX internal Node links require POSIX filesystem semantics")
def test_native_node_link_install_and_link_target_generation_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "RUNTIMES_DIR", tmp_path / "runtimes")
    data = node_tar()
    monkeypatch.setattr(runtime, "_download", lambda _u, p, _s=None: p.write_bytes(data))
    plan = runtime.make_archive_runtime_plan("node", version="v1.0.0", url="https://example.invalid/node.tar.gz",
        sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), asset_name="node.tar.gz", executable_candidates=("bin/node",))
    result = runtime.install_runtime_plan(plan)
    npm = Path(result.bin_dir) / "npm"
    assert npm.is_symlink() and npm.read_bytes() == b"script"
    assert runtime._managed_bin_dir("node") == Path(result.bin_dir)
    npm.resolve().write_bytes(b"changed")
    assert runtime._managed_bin_dir("node") is None


@pytest.mark.parametrize("runtime_id", ["node", "uv"])
def test_legacy_install_resolves_once_and_publishes_actual_platform_layout(tmp_path, monkeypatch, runtime_id):
    import json
    monkeypatch.setattr(runtime, "RUNTIMES_DIR", tmp_path / "runtimes")
    windows = runtime.platform.system().lower() == "windows"
    version = "v1.2.3" if runtime_id == "node" else "0.1.0"
    asset_name = runtime._node_asset_name(version)[0] if runtime_id == "node" else (
        "uv-" + runtime._uv_asset_fragment() + (".zip" if windows else ".tar.gz"))
    binary = "node.exe" if windows else "bin/node"
    if runtime_id == "uv":
        binary = "uv.exe" if windows else "uv"
    members = {"bundle/" + binary: b"synthetic executable"}
    if runtime_id == "node" and windows:
        members.update({"bundle/npm.cmd": b"synthetic npm", "bundle/npx.cmd": b"synthetic npx"})
    data = archive(members)
    digest = hashlib.sha256(data).hexdigest()
    archive_url = (f"https://nodejs.org/dist/{version}/{asset_name}" if runtime_id == "node" else
        f"https://github.com/astral-sh/uv/releases/download/{version}/{asset_name}")
    calls = []
    class Response(io.BytesIO):
        headers = {"Content-Length": str(len(data))}
    def request(url, *, method=None):
        calls.append((url, method))
        if method == "HEAD":
            assert url == archive_url
            return Response(b"")
        if url == "https://nodejs.org/dist/index.json":
            return Response(json.dumps([{"version":version,"lts":"Synthetic"}]).encode())
        if url.endswith("SHASUMS256.txt"):
            return Response(f"{digest}  {asset_name}\n".encode())
        if url == "https://api.github.com/repos/astral-sh/uv/releases/latest":
            return Response(json.dumps({"tag_name":version,"assets":[{"name":asset_name,
                "browser_download_url":archive_url,"size":len(data),"digest":"sha256:"+digest}]}).encode())
        assert url == archive_url
        return Response(data)
    monkeypatch.setattr(runtime, "_request", request)
    result = runtime.install_managed_runtime(runtime_id)
    assert result.ok, result
    saved = runtime._read_manifest(runtime_id)
    assert saved["archive_sha256"] == digest and saved["archive_size"] == len(data)
    assert Path(saved["executable_path"]).read_bytes() == b"synthetic executable"
    assert calls.count((archive_url, None)) == 1
    assert len(calls) == (4 if runtime_id == "node" else 2)


def test_missing_node_checksum_and_uv_digest_never_download(owner, monkeypatch):
    _, _, calls, _ = owner
    monkeypatch.setattr(runtime, "_latest_node_lts_version", lambda: "v1.2.3")
    monkeypatch.setattr(runtime, "_remote_bytes", lambda _url: b"not a matching checksum\n")
    with pytest.raises(RuntimeError, match="checksum"):
        runtime.resolve_managed_runtime_plan("node")
    monkeypatch.setattr(runtime, "_uv_release_asset", lambda: ("0.1.0", {"name": "uv.zip", "size": 100, "browser_download_url": "https://example.invalid/uv.zip"}))
    with pytest.raises(RuntimeError, match="digest"):
        runtime.resolve_managed_runtime_plan("uv")
    assert not calls


def test_streaming_download_and_metadata_enforce_actual_byte_budgets(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_request", lambda *_a, **_k: io.BytesIO(b"x" * 129))
    monkeypatch.setattr(runtime, "ARCHIVE_BYTE_LIMIT", 128)
    with pytest.raises(RuntimeError, match="budget"):
        runtime._download("https://example.invalid/runtime.zip", tmp_path / "download")
    assert (tmp_path / "download").stat().st_size <= 128
    with pytest.raises(RuntimeError, match="budget"):
        runtime._remote_bytes("https://example.invalid/metadata", maximum=128)


def test_streaming_metadata_deadline_is_observed_between_received_chunks(monkeypatch):
    ticks = iter((0, 31))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(runtime, "_request", lambda *_a, **_k: io.BytesIO(b"x"))
    with pytest.raises(RuntimeError, match="budget"):
        runtime._remote_bytes("https://example.invalid/metadata")


@pytest.mark.parametrize("kind", ["entry", "expanded"])
def test_archive_aggregate_budget_rejects_before_creating_members(tmp_path, monkeypatch, kind):
    source = tmp_path / "source.zip"
    source.write_bytes(archive({"a": b"abc", "b": b"abc"}))
    monkeypatch.setattr(runtime, "ARCHIVE_ENTRY_LIMIT" if kind == "entry" else "EXTRACTED_BYTE_LIMIT", 1)
    with pytest.raises(RuntimeError, match="budget|bounds"):
        runtime._extract_archive(source, tmp_path / "output")
    assert not list((tmp_path / "output").iterdir())


@pytest.mark.parametrize("document", [b'{"version":1,"version":2}', b'{"unknown":NaN}', b'[]', b''])
def test_corrupt_manifest_is_preserved_and_prevents_plan(owner, document):
    _, _, calls, make = owner
    root = runtime.RUNTIMES_DIR / "synthetic"
    root.mkdir(parents=True)
    (root / "manifest.json").write_bytes(document)
    with pytest.raises((RuntimeError, ValueError)):
        make()
    assert not calls and (root / "manifest.json").read_bytes() == document


def test_generation_change_in_final_publication_callback_is_not_reported_installed(owner, monkeypatch):
    _, _, _, make = owner
    original = runtime._write_manifest
    def change(runtime_id, document, **kwargs):
        Path(document["executable_path"]).write_bytes(b"external edit before publication")
        return original(runtime_id, document, **kwargs)
    monkeypatch.setattr(runtime, "_write_manifest", change)
    with pytest.raises(RuntimeError, match="generation changed"):
        runtime.install_runtime_plan(make())
    assert runtime._read_manifest("synthetic") == {}
    assert (runtime.RUNTIMES_DIR / "synthetic/1.2.3/runtime.exe").read_bytes() == b"external edit before publication"


def test_cancellation_after_download_never_activates_generation(owner, monkeypatch):
    _, data, _, make = owner
    cancelled = False
    def download(_url, path, _progress=None):
        nonlocal cancelled
        path.write_bytes(data)
        cancelled = True
    monkeypatch.setattr(runtime, "_download", download)
    result = runtime.install_runtime_plan(make(), cancelled=lambda: cancelled)
    assert not result.ok and not runtime.RUNTIMES_DIR.exists()


def test_explicit_plan_checks_authority_before_downloading(owner):
    _, _, _, make = owner
    revoked = False
    def validate():
        if revoked:
            raise PermissionError("revoked")
    plan = make()
    revoked = True
    with pytest.raises(PermissionError, match="revoked"):
        runtime.install_runtime_plan(plan, validate=validate)
    assert not runtime.RUNTIMES_DIR.exists()


def test_revocation_while_waiting_for_real_install_lock_prevents_directory_effects(owner):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    _, _, _, make = owner
    plan = make()
    arrived, revoked = threading.Event(), threading.Event()
    calls = 0
    def validate():
        nonlocal calls
        calls += 1
        if calls == 2:
            arrived.set()
            return
        if revoked.is_set():
            raise PermissionError("revoked during lock wait")
    with ThreadPoolExecutor(max_workers=1) as executor:
        with runtime._INSTALL_LOCK:
            result = executor.submit(runtime.install_runtime_plan, plan, validate=validate)
            assert arrived.wait(5)
            revoked.set()
        with pytest.raises(PermissionError, match="lock wait"):
            result.result(timeout=10)
    assert not runtime.RUNTIMES_DIR.exists()


@pytest.mark.parametrize("legacy", [False, True])
def test_private_download_directory_os_alias_is_canonicalized(owner, monkeypatch, legacy):
    from contextlib import contextmanager
    tmp, _, _, make = owner
    target = tmp / "physical-temp"
    target.mkdir()
    alias = tmp / "os-temp-alias"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(alias))
    else:
        alias.symlink_to(target, target_is_directory=True)

    @contextmanager
    def temporary_directory(**_kwargs):
        # Emulate a fresh private temp directory beneath the OS temp alias.
        actual = target / "private-created-directory"
        actual.mkdir()
        yield str(alias / actual.name)

    monkeypatch.setattr(runtime.tempfile, "TemporaryDirectory", temporary_directory)
    plan = make()
    if legacy:
        result = runtime.install_pinned_archive_runtime(plan.runtime_id,
            version=plan.version, url=plan.url, sha256=plan.sha256,
            asset_name=plan.asset_name, executable_candidates=plan.executable_candidates)
    else:
        result = runtime.install_runtime_plan(plan)
    assert result.ok
    assert (target / "private-created-directory/runtime.zip").is_file()
    assert runtime._managed_bin_dir(plan.runtime_id) is not None
