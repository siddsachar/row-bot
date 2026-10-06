"""Locked package preparation (npm, PyPI, container images) with synthetic archives and faked tools:
the lock is resolved without running the package, reviewed, then installed exactly, privately."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

from row_bot.integrations.safe import TtlCache
from row_bot.mcp_client import packages
from row_bot.plugins import hermes_mcp

pytestmark = pytest.mark.platform


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as output:
        for name, value in files.items():
            data = value.encode()
            info = tarfile.TarInfo("package/" + name)
            info.size = len(data)
            output.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def integrity(raw):
    return "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode()


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(packages, "_PREVIEWS", TtlCache(1200, 32, full="mcp_package_preview_capacity"))
    (tmp_path / "mcp_packages").mkdir()
    return tmp_path


class Tools:
    """npm, uv and docker as recorded calls: each writes what the real tool would, and nothing runs."""

    def __init__(self, monkeypatch, data):
        self.calls, self.data, self.lock, self.compiled = [], data, None, ""
        monkeypatch.setattr(packages, "_run", self.run)
        monkeypatch.setattr(packages, "_node", lambda: ("node", str(data / "npm-cli.js")))
        monkeypatch.setattr(packages, "_uv", lambda: "uv")
        monkeypatch.setattr(packages.shutil, "which", lambda name: "docker" if name == "docker" else None)

    def run(self, argv, *, env, cwd=None, stdin="", timeout=300, check=lambda: None):
        self.calls.append({"argv": argv, "env": env, "cwd": cwd, "stdin": stdin})
        if argv[1:3] == [str(self.data / "npm-cli.js"), "install"]:
            (Path(cwd) / "package-lock.json").write_text(json.dumps(self.lock))
        elif argv[:2] == ["uv", "venv"]:
            python = Path(argv[2]) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("python")
        elif argv[:3] == ["uv", "pip", "compile"]:
            return self.compiled
        elif argv[:3] == ["uv", "pip", "install"]:
            venv = Path(argv[argv.index("--python") + 1]).parent.parent
            (venv / "Lib/site-packages/fixture").mkdir(parents=True, exist_ok=True)
            (venv / "Lib/site-packages/fixture/__init__.py").write_text("SERVER = 1")
            script = venv / ("Scripts/fixture-mcp.exe" if sys.platform == "win32" else "bin/fixture-mcp")
            script.write_text("launcher")
        elif argv[:3] == ["docker", "image", "inspect"]:
            return json.dumps(["ghcr.io/example/notes@sha256:" + "a" * 64])
        return ""


@pytest.fixture
def tools(data, monkeypatch):
    return Tools(monkeypatch, data)


def registry(monkeypatch, tarballs: dict, metadata: dict):
    calls = []

    def fetch(url, **_):
        calls.append(url)
        if url.endswith(".tgz"):
            return tarballs[url]
        return json.dumps(metadata).encode()
    monkeypatch.setattr(packages, "_fetch", fetch)
    return calls


NPM = {"transport": "stdio", "command": "npx", "args": ["-y", "fixture-mcp@1.0.0", "--fast"], "environment_mode": "minimal"}


def self_contained(monkeypatch, extra=None, corrupt=False):
    manifest = {"name": "fixture-mcp", "version": "1.0.0", "license": "MIT", "bin": "main.js", **(extra or {})}
    raw = archive({"package.json": json.dumps(manifest), "main.js": "process.exit(0)"})
    url = "https://registry.npmjs.org/fixture-mcp/-/fixture-mcp-1.0.0.tgz"
    return registry(monkeypatch, {url: raw + b"changed" if corrupt else raw},
                    {**manifest, "dist": {"tarball": url, "integrity": integrity(raw)}})


def test_a_self_contained_package_is_locked_installed_exactly_and_bound_to_its_connection(data, tools, monkeypatch):
    calls = self_contained(monkeypatch)
    lock = packages.resolve(NPM)
    assert [(i["name"], i["version"]) for i in lock["items"]] == [("fixture-mcp", "1.0.0")] and not tools.calls
    shown = packages.review(lock)
    assert shown["items"][0]["integrity"].startswith("sha512-") and shown["digest"] == lock["digest"]
    reviewed = packages.inspect("owner", NPM, lock)
    assert len(calls) == 2 and reviewed["dependencies"] == 0 and reviewed["lock_digest"] == lock["digest"]
    launch = packages.reviewed_launch("owner", reviewed["preview_id"], NPM, reviewed["digest"])
    assert launch["kind"] == "npm" and launch["integrity"].startswith("sha512-") and launch["args"] == ["--fast"]
    with pytest.raises(ValueError, match="preview_expired"):
        packages.reviewed_launch("other", reviewed["preview_id"], NPM, reviewed["digest"])
    with pytest.raises(ValueError, match="integrity_changed"):
        packages.reviewed_launch("owner", reviewed["preview_id"], {**NPM, "args": ["other"]}, reviewed["digest"])
    assert len(calls) == 2  # Acceptance never downloads again.


def test_install_scripts_are_disclosed_and_never_run_and_changed_bytes_are_refused(data, tools, monkeypatch):
    self_contained(monkeypatch, {"scripts": {"postinstall": "arbitrary-bootstrap"}})
    lock = packages.resolve(NPM)
    assert any("install scripts" in line for line in packages.review(lock)["lines"])
    packages.inspect("owner", NPM, lock)
    assert not tools.calls  # No npm, no node: the tarball is only unpacked.
    self_contained(monkeypatch, corrupt=True)
    with pytest.raises(ValueError, match="integrity_changed"):
        packages.inspect("owner", NPM, packages.resolve(NPM))


def test_dependencies_without_a_shrinkwrap_are_locked_by_npm_with_scripts_off_in_a_private_folder(data, tools, monkeypatch):
    main = archive({"package.json": json.dumps({"name": "fixture-mcp", "version": "1.0.0", "bin": {"fixture-mcp": "main.js"}}),
                    "main.js": "require('dep')"})
    dep = archive({"package.json": json.dumps({"name": "dep", "version": "2.0.0"}), "index.js": "module.exports = 1"})
    base = "https://registry.npmjs.org/"
    tools.lock = {"lockfileVersion": 3, "packages": {
        "": {"dependencies": {"fixture-mcp": "1.0.0"}},
        "node_modules/fixture-mcp": {"version": "1.0.0", "resolved": base + "main.tgz", "integrity": integrity(main)},
        "node_modules/dep": {"version": "2.0.0", "resolved": base + "dep.tgz", "integrity": integrity(dep), "hasInstallScript": True},
        "node_modules/other-os": {"version": "1.0.0", "optional": True, "os": ["aix"], "resolved": base + "x.tgz",
                                  "integrity": integrity(b"x")}}}
    registry(monkeypatch, {base + "main.tgz": main, base + "dep.tgz": dep},
             {"name": "fixture-mcp", "version": "1.0.0", "dependencies": {"dep": "^2"}, "dist": {"tarball": base + "main.tgz",
              "integrity": integrity(main)}})
    lock = packages.resolve(NPM)
    (call,) = tools.calls
    assert {"--package-lock-only", "--ignore-scripts"} <= set(call["argv"])
    assert call["env"]["npm_config_ignore_scripts"] == "true" and call["env"]["npm_config_userconfig"].startswith(str(data))
    assert call["env"]["npm_config_cache"].startswith(str(data)) and Path(call["cwd"]).is_relative_to(data)
    assert not Path(call["cwd"]).exists()  # The resolving folder is gone.
    assert [i["name"] for i in lock["items"]] == ["fixture-mcp", "dep"]  # Another platform's optional binary is left out.
    reviewed = packages.inspect("owner", NPM, lock)
    root = data / "mcp_packages" / reviewed["preview_id"]
    assert (root / "node_modules/dep/index.js").is_file() and len(tools.calls) == 1
    launch = packages.reviewed_launch("owner", reviewed["preview_id"], NPM, reviewed["digest"])
    assert launch["entry"] == "node_modules/fixture-mcp/main.js"
    tools.lock["packages"]["node_modules/dep"]["resolved"] = "https://evil.example.test/dep.tgz"
    with pytest.raises(ValueError, match="integrity_required"):
        packages.resolve(NPM)


PYPI = {"transport": "stdio", "command": "uvx", "args": ["--from", "fixture-mcp==1.0.0", "fixture-mcp", "--fast"],
        "environment_mode": "minimal"}


def test_a_python_package_installs_only_hashed_wheels_into_its_own_environment(data, tools):
    tools.compiled = ("fixture-mcp==1.0.0 \\\n    --hash=sha256:" + "1" * 64 + "\nhelper==2.1 \\\n    --hash=sha256:" + "2" * 64
                      + " \\\n    --hash=sha256:" + "3" * 64 + "\n")
    lock = packages.resolve(PYPI)
    assert [(i["name"], i["version"]) for i in lock["items"]] == [("fixture-mcp", "1.0.0"), ("helper", "2.1")]
    compile_call = next(c for c in tools.calls if c["argv"][:3] == ["uv", "pip", "compile"])
    assert {"--generate-hashes", ":all:"} <= set(compile_call["argv"]) and compile_call["stdin"] == "fixture-mcp==1.0.0\n"
    reviewed = packages.inspect("owner", PYPI, lock)
    install = next(c for c in tools.calls if c["argv"][:3] == ["uv", "pip", "install"])
    assert {"--require-hashes", "--no-deps", "--only-binary", ":all:"} <= set(install["argv"]) and "--system" not in install["argv"]
    for call in tools.calls:
        assert call["env"]["UV_NO_CONFIG"] == "1" and call["env"]["UV_CACHE_DIR"].startswith(str(data))
        assert call["env"]["UV_PYTHON_INSTALL_DIR"].startswith(str(data))
    root = data / "mcp_packages" / reviewed["preview_id"]
    assert "--hash=sha256:" + "3" * 64 in (root / "requirements.txt").read_text()
    cfg = {**PYPI, "managed_launch": packages.reviewed_launch("owner", reviewed["preview_id"], PYPI, reviewed["digest"])}
    command, args = packages.resolve_launch(cfg)
    assert Path(command).is_relative_to(root) and args == ["--fast"]
    (root / "site/Lib/site-packages/fixture/__pycache__").mkdir()
    (root / "site/Lib/site-packages/fixture/__pycache__/x.pyc").write_bytes(b"planted")
    with pytest.raises(ValueError, match="integrity_changed"):  # Launches write no byte-code, so any is a change.
        packages.resolve_launch(cfg)
    (root / "site/Lib/site-packages/fixture/__init__.py").write_text("SERVER = 2")
    with pytest.raises(ValueError, match="integrity_changed"):
        packages.resolve_launch(cfg)


def test_a_container_runs_by_digest_with_no_access_to_this_computer(data, tools, monkeypatch):
    monkeypatch.setenv("APPDATA", "C:/Users/fixture/AppData/Roaming")
    for name in ("LOCALAPPDATA", "PROGRAMDATA", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    oci = {"transport": "stdio", "command": "docker", "env": {"TOKEN": "{token}"},
           "args": ["run", "-i", "--rm", "--cap-drop", "ALL", "-e", "TOKEN", "ghcr.io/example/notes:1.0.0", "serve"]}
    lock = packages.resolve(oci)
    assert lock["items"][0]["integrity"] == "sha256:" + "a" * 64 and [c["argv"][1] for c in tools.calls] == ["pull", "image"]
    reviewed = packages.inspect("owner", oci, lock)
    cfg = {**oci, "managed_launch": packages.reviewed_launch("owner", reviewed["preview_id"], oci, reviewed["digest"])}
    command, args = packages.resolve_launch(cfg)
    assert command == "docker" and args == ["run", "-i", "--rm", "--pull=never", "--cap-drop", "ALL", "-e", "TOKEN",
                                            "ghcr.io/example/notes@sha256:" + "a" * 64, "serve"]
    for flags in (["-v", "/:/host"], ["--network", "host"], ["--privileged"], ["-p", "8080:8080"], ["-e", "DOCKER_HOST"]):
        with pytest.raises(ValueError, match="container_access_unsupported"):
            packages.resolve({**oci, "args": ["run", "-i", "--rm", *flags, "ghcr.io/example/notes:1.0.0"]})
    # The Docker CLI gets only what it forwards into the container, never a recipe's DOCKER_HOST, plus what
    # its own sign-in helper needs to find the person's Docker sign-in (never forwarded into the container).
    env = packages.launch_environment(cfg, {"PATH": "p", "TOKEN": "t", "DOCKER_HOST": "tcp://203.0.113.5:2375", "OTHER": "o"})
    assert env == {"PATH": "p", "TOKEN": "t", "APPDATA": "C:/Users/fixture/AppData/Roaming"}
    assert "APPDATA" not in args and tools.calls[0]["env"]["APPDATA"] == "C:/Users/fixture/AppData/Roaming"


def test_a_docker_sign_in_helper_that_fails_is_named_plainly(data, tools, monkeypatch):
    from row_bot.integrations import plans

    def refused(argv, **kwargs):
        raise ValueError("mcp_package_tool_failed: error getting credentials - err: exec: docker-credential-desktop")
    monkeypatch.setattr(packages, "_run", refused)
    with pytest.raises(ValueError, match="mcp_package_docker_credentials") as raised:
        packages.resolve({"transport": "stdio", "command": "docker", "args": ["run", "-i", "--rm", "ghcr.io/x/y:1.0"]})
    assert "sign-in helper" in plans._package_problem(str(raised.value))


def test_launch_never_fetches_a_package_without_explicit_preparation(data):
    for cfg in (NPM, PYPI, {"transport": "stdio", "command": "docker", "args": ["run", "-i", "--rm", "ghcr.io/x/y:1.0"]}):
        with pytest.raises(ValueError, match="preparation_required"):
            packages.resolve_launch(cfg)


def test_hermes_recipe_maps_https_and_refuses_bootstrap():
    recipe = {"manifest_version": 1, "name": "fixture", "transport": {"type": "http", "url": "https://example.test/mcp"}, "auth": {"type": "oauth"}}
    result = hermes_mcp.normalize_recipe(recipe, name="fixture", pin="a" * 40, source_url="https://example.test/manifest")
    cfg = json.loads(result["import_json"])["mcpServers"]["fixture"]
    assert not cfg["enabled"] and result["requires_auth"] and cfg["source"]["pin"] == "a" * 40
    with pytest.raises(ValueError, match="recipe_unsupported"):
        hermes_mcp.normalize_recipe({**recipe, "install": {"bootstrap": ["run-me"]}}, name="fixture", pin="a" * 40, source_url="")
    local = {"manifest_version": 1, "name": "fixture", "auth": {"type": "none"}, "transport": {
        "type": "stdio", "command": "npx", "args": ["-y", "fixture-mcp@1.0.0"], "env": {"NODE_OPTIONS": "--import=data:x"}}}
    with pytest.raises(ValueError, match="recipe_unsupported"):  # Never a variable that changes what runs.
        hermes_mcp.normalize_recipe(local, name="fixture", pin="a" * 40, source_url="")
