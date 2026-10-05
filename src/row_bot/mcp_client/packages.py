"""Nonexecuting preparation of locked MCP packages: npm, PyPI and container images.

Preparing a package has three parts, and no code from the package runs in any of them:

1. ``resolve``: the exact versions and their integrity hashes, read from the package's registry
   (npm metadata or a lock-only ``npm install --ignore-scripts``, ``uv pip compile --generate-hashes``
   of prebuilt wheels, or ``docker pull`` for an image digest).
2. The person reviews that lock; the plan pauses with it.
3. ``inspect``: exactly those bytes are fetched, checked against their integrity and laid out in a
   private folder under the data folder; ``reviewed_launch`` binds the folder to the connection, and
   ``resolve_launch`` refuses to start a tree that changed since.

Install scripts never run (npm), only prebuilt wheels are installed with ``--require-hashes`` (PyPI),
images run by digest and are never pulled again at launch (Docker), and nothing is installed globally:
every cache and configuration the tools use lives in Row-Bot's own data folder.
"""
from __future__ import annotations

import base64
from collections.abc import Callable
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
from urllib.parse import quote
from uuid import uuid4

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.integrations.safe import TtlCache, fetch
from row_bot.package_files import check_package_tree, contained_path, relative_package_path

_NAME = r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+"
_PREVIEWS = TtlCache(1200, 32, full="mcp_package_preview_capacity")
_MAX = 64 * 1024 * 1024
_TREE = {"max_files": 60000, "max_bytes": 1024 * 1024 * 1024}  # A locked dependency tree, not one archive.
NPM_REGISTRY = "https://registry.npmjs.org/"
PYPI_INDEX = "https://pypi.org/simple"
PYTHON = "3.12"


def requirement(cfg: dict) -> tuple[str, str, list[str]] | None:
    command = Path(str(cfg.get("command", ""))).name.lower().removesuffix(".cmd").removesuffix(".exe")
    if command not in {"npx", "uvx", "npm", "uv", "pip", "pip3"}:
        return None
    args = list(cfg.get("args", []))
    if command != "npx":
        raise ValueError("mcp_package_recipe_unsupported")
    if args[:1] in (["-y"], ["--yes"]):
        args.pop(0)
    if not args or not isinstance(args[0], str):
        raise ValueError("mcp_package_recipe_unsupported")
    match = re.fullmatch(r"(" + _NAME + r")(?:@([A-Za-z0-9_.+-]+))?", args[0])
    if not match or any(not isinstance(v, str) or len(v) > 4096 for v in args[1:]) or len(args) > 128:
        raise ValueError("mcp_package_recipe_unsupported")
    return match[1], match[2] or "latest", args[1:]


def kind(cfg: dict) -> str | None:
    """Which locked package a stdio recipe runs: ``npm`` (npx), ``pypi`` (uvx), ``oci`` (docker run) or
    ``mcpb`` (an MCP bundle the person picked)."""
    if cfg.get("bundle") and cfg.get("transport", "stdio") == "stdio":
        return "mcpb"
    command = Path(str(cfg.get("command", ""))).name.lower().removesuffix(".cmd").removesuffix(".exe")
    return {"npx": "npm", "uvx": "pypi", "docker": "oci"}.get(command) if cfg.get("transport", "stdio") == "stdio" else None


def _pypi(cfg: dict) -> tuple[str, str, list[str]]:
    """``uvx --from name==version entry args…``: a pinned requirement and its console script."""
    args = [str(arg) for arg in cfg.get("args", [])]
    if args[:1] == ["--from"] and len(args) >= 3:
        spec, entry, rest = args[1], args[2], args[3:]
    else:
        spec, rest = (args[0] if args else ""), args[1:]
        entry = re.split(r"[=@\[]", spec, maxsplit=1)[0]
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}(\[[A-Za-z0-9,_-]+\])?(==[A-Za-z0-9.!+_-]{1,64})?", spec)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", entry)):
        raise ValueError("mcp_package_recipe_unsupported")  # The lock pins whatever version this resolves to.
    return spec, entry, rest


def _oci(cfg: dict) -> tuple[str, list[str], list[str]]:
    """``docker run -i --rm [narrowing flags] [-e NAME…] image args…``, the only shape Row-Bot runs."""
    args = [str(arg) for arg in cfg.get("args", [])]
    if args[:3] != ["run", "-i", "--rm"]:
        raise ValueError("mcp_package_recipe_unsupported")
    flags, index = [], 3
    while index < len(args) and args[index].startswith("-"):
        flag = args[index]
        if flag == "-e" and index + 1 < len(args) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", args[index + 1]):
            flags += args[index:index + 2]
            index += 2
        elif flag in {"--cap-drop", "--security-opt"} and index + 1 < len(args) and args[index + 1] in {"ALL", "no-new-privileges"}:
            flags += args[index:index + 2]
            index += 2
        elif flag in {"--read-only", "--init"}:
            flags.append(flag)
            index += 1
        else:  # Folders, ports, devices, privileges or the host network are never granted.
            raise ValueError("mcp_package_container_access_unsupported")
    if index >= len(args) or not re.fullmatch(r"[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)+(:[A-Za-z0-9._-]{1,128}|@sha256:[0-9a-f]{64})",
                                               args[index]):
        raise ValueError("mcp_package_recipe_unsupported")
    return args[index], flags, args[index + 1:]


def _fetch(url: str, *, maximum: int = _MAX) -> bytes:
    return fetch(url, hosts={"registry.npmjs.org"}, max_bytes=maximum, timeout=30,
        refused="mcp_package_source_invalid", too_large="mcp_package_too_large")


def _folder(name: str) -> Path:
    return contained_path(get_row_bot_data_dir(create=False), "mcp_packages/" + name)


def _run(argv: list[str], *, env: dict, cwd: Path | None = None, stdin: str = "", timeout: float = 300,
         check: Callable[[], None] = lambda: None) -> str:
    """One tool run (npm, uv or docker) with a private environment, no shell and no window; stopping
    the plan stops it. Returns its output, or raises with the tool's last line of error."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, creationflags=flags)
    deadline = time.monotonic() + timeout
    try:
        out, err = process.communicate(stdin.encode(), timeout=0.2)
    except subprocess.TimeoutExpired:
        try:
            while True:
                check()
                if time.monotonic() > deadline:
                    raise TimeoutError("mcp_package_tool_timeout")
                try:
                    out, err = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            process.kill()
            process.communicate()
            raise
    if process.returncode:
        text = err.decode("utf-8", "replace")
        last = next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")
        raise ValueError("mcp_package_tool_failed: " + re.sub(r"[A-Za-z]:\\\S+|/\S+/", "…", last)[:200])
    return out.decode("utf-8", "replace")


def _env(**extra: str) -> dict:
    """A tool's environment: the system basics only, plus Row-Bot's own caches and configuration."""
    keep = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL",
            "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy"}
    return {**{key: value for key, value in os.environ.items() if key in keep}, **extra}


def _digest(items: list[dict]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(items, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --- Resolve: the lock the person reviews -------------------------------------------------------------

def resolve(cfg: dict, *, check: Callable[[], None] = lambda: None) -> dict:
    """The exact versions and integrity a recipe would install, for the person to review. Reads registry
    metadata (and runs npm, uv or Docker only to resolve); never installs or runs the package."""
    found = {"npm": _npm_lock, "pypi": _pypi_lock, "oci": _oci_lock, "mcpb": _mcpb_lock}.get(kind(cfg) or "")
    if found is None:
        raise ValueError("mcp_package_recipe_unsupported")
    lock = found(cfg, check)
    lock["digest"] = _digest([{key: item.get(key) for key in ("path", "name", "version", "integrity")} for item in lock["items"]]
                             + [{"kind": lock["kind"], "entry": lock.get("entry", "")}])
    return lock


def _npm_lock(cfg: dict, check: Callable[[], None]) -> dict:
    name, version, _args = requirement(cfg) or ("", "", [])
    metadata = json.loads(_fetch(NPM_REGISTRY + quote(name, safe="@") + "/" + quote(version, safe=""), maximum=4 * 1024 * 1024))
    if metadata.get("name") != name or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", metadata.get("version", "")):
        raise ValueError("mcp_package_invalid")
    dist = metadata.get("dist") or {}
    root = {"path": "", "name": name, "version": metadata["version"], "resolved": dist.get("tarball", ""),
            "integrity": dist.get("integrity", ""), "scripts": _has_scripts(metadata)}
    if not any(metadata.get(key) for key in ("dependencies", "optionalDependencies", "peerDependencies")):
        items, layout = [root], "package"
    elif metadata.get("_hasShrinkwrap"):  # The publisher's own complete lock, inside the reviewed tarball.
        items, layout = [root, *_shrinkwrap(root)], "package"
    else:
        items, layout = _npm_resolve(name, metadata["version"], check), "wrapper"
    for item in items:
        if (not str(item["resolved"]).startswith(NPM_REGISTRY) or not re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", str(item["integrity"]))
                or (item["path"] and not item["path"].startswith("node_modules/"))):
            raise ValueError("mcp_package_integrity_required")
        if item["path"]:
            relative_package_path(item["path"])
    if len(items) > 4000:
        raise ValueError("mcp_package_too_large")
    return {"kind": "npm", "layout": layout, "name": name, "version": metadata["version"], "items": items,
            "entry": "", "integrity": root["integrity"]}


def _has_scripts(manifest: dict) -> bool:
    return any(key in (manifest.get("scripts") or {}) for key in ("preinstall", "install", "postinstall"))


def _shrinkwrap(root: dict) -> list[dict]:
    raw = _verified(root["resolved"], root["integrity"])
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        member = next((m for m in archive if m.name.removeprefix("./") == "package/npm-shrinkwrap.json" and m.isfile()), None)
        content = archive.extractfile(member) if member is not None and member.size <= 4 * 1024 * 1024 else None
        if content is None:
            raise ValueError("mcp_package_locked_dependencies_required")
        lock = json.loads(content.read())
    return _lock_items(lock)


def _lock_items(lock: dict) -> list[dict]:
    """The installable entries of an npm lockfile: registry tarballs with integrity, this platform's only."""
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if lock.get("lockfileVersion") not in {2, 3} or type(packages) is not dict or len(packages) > 4000:
        raise ValueError("mcp_package_locked_dependencies_required")
    platform = {"win32": "win32", "darwin": "darwin"}.get(sys.platform, "linux")
    items = []
    for path, value in packages.items():
        if not path or value.get("inBundle") or value.get("dev"):
            continue
        if value.get("link") or not isinstance(value.get("version"), str):
            raise ValueError("mcp_package_recipe_unsupported")
        allowed = value.get("os") or []
        if value.get("optional") and allowed and platform not in allowed and not any(name.startswith("!") for name in allowed):
            continue  # Another platform's optional binary.
        items.append({"path": path, "name": str(value.get("name") or path.rsplit("node_modules/", 1)[-1]),
                      "version": value["version"], "resolved": str(value.get("resolved") or ""),
                      "integrity": str(value.get("integrity") or ""), "scripts": bool(value.get("hasInstallScript"))})
    return items


def _node() -> tuple[str, str]:
    """The managed (or system) Node and its own npm, run as ``node npm-cli.js`` with no shell."""
    from row_bot.mcp_client.requirements import managed_command_path
    node = managed_command_path("node", "node") or shutil.which("node")
    if not node:
        raise ValueError("mcp_package_node_required")
    base = Path(node).resolve().parent
    for candidate in (base / "node_modules/npm/bin/npm-cli.js", base.parent / "lib/node_modules/npm/bin/npm-cli.js"):
        if candidate.is_file():
            return node, str(candidate)
    raise ValueError("mcp_package_npm_unavailable")


def _npm_resolve(name: str, version: str, check: Callable[[], None]) -> list[dict]:
    """A lock-only npm resolution in a throwaway folder: no package is downloaded or run, scripts are off,
    and npm's cache and settings are Row-Bot's own, never the user's."""
    node, npm = _node()
    staging = _folder(".resolve-" + uuid4().hex)
    staging.mkdir(parents=True)
    try:
        (staging / "package.json").write_text(json.dumps({"name": "row-bot-resolve", "version": "0.0.0", "private": True,
                                                           "dependencies": {name: version}}), encoding="utf-8")
        (staging / ".npmrc").write_text("", encoding="utf-8")
        env = _env(npm_config_cache=str(_folder(".npm-cache")), npm_config_userconfig=str(staging / ".npmrc"),
                   npm_config_globalconfig=str(staging / ".npmrc"), npm_config_registry=NPM_REGISTRY,
                   npm_config_ignore_scripts="true", npm_config_update_notifier="false", npm_config_fund="false",
                   npm_config_audit="false", PATH=str(Path(node).parent) + os.pathsep + os.environ.get("PATH", ""))
        _run([node, npm, "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund",
              "--registry=" + NPM_REGISTRY], env=env, cwd=staging, timeout=300, check=check)
        lock = json.loads(contained_path(staging, "package-lock.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    items = _lock_items(lock)
    if not any(item["path"] == "node_modules/" + name for item in items):
        raise ValueError("mcp_package_invalid")
    return items


def _uv() -> str:
    from row_bot.mcp_client.requirements import managed_command_path
    uv = managed_command_path("uv", "uv") or shutil.which("uv")
    if not uv:
        raise ValueError("mcp_package_uv_required")
    return uv


def _uv_env() -> dict:
    """uv with Row-Bot's own cache and managed Python, ignoring any user or project configuration."""
    data = get_row_bot_data_dir(create=False)
    return _env(UV_NO_CONFIG="1", UV_CACHE_DIR=str(_folder(".uv-cache")), UV_PYTHON_INSTALL_DIR=str(data / "runtimes" / "uv-python"),
                UV_PYTHON_PREFERENCE="only-managed", UV_NO_PROGRESS="1", PYTHONDONTWRITEBYTECODE="1")


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _pypi_lock(cfg: dict, check: Callable[[], None]) -> dict:
    spec, entry, _args = _pypi(cfg)
    uv = _uv()
    staging = _folder(".resolve-" + uuid4().hex)
    staging.mkdir(parents=True)
    try:
        _run([uv, "venv", str(staging / "venv"), "--python", PYTHON], env=_uv_env(), timeout=600, check=check)
        text = _run([uv, "pip", "compile", "-", "--python", str(_venv_python(staging / "venv")), "--generate-hashes",
                     "--no-header", "--no-annotate", "--only-binary", ":all:", "--index-url", PYPI_INDEX, "--quiet"],
                    env=_uv_env(), stdin=spec + "\n", timeout=300, check=check)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    items, current = [], None
    for line in text.splitlines():
        line = line.strip().rstrip("\\").strip()
        pinned = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,_-]+\])?)==([A-Za-z0-9.!+_-]+)", line)
        if pinned:
            current = {"path": "", "name": pinned[1].lower(), "version": pinned[2], "hashes": []}
            items.append(current)
        elif line.startswith("--hash=") and current is not None and re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", line):
            current["hashes"].append(line.removeprefix("--hash="))
        elif line and not line.startswith("#"):
            raise ValueError("mcp_package_lock_invalid")
    if not items or any(not item["hashes"] for item in items) or len(items) > 500:
        raise ValueError("mcp_package_lock_invalid")
    for item in items:
        item["integrity"] = item["hashes"][0]
    name = spec.split("==", 1)[0].split("[", 1)[0].lower()
    version = next((item["version"] for item in items if item["name"].split("[", 1)[0] == name), "")
    return {"kind": "pypi", "name": name, "version": version, "items": items, "entry": entry,
            "integrity": next((item["integrity"] for item in items if item["name"].split("[", 1)[0] == name), items[0]["integrity"])}


def _oci_lock(cfg: dict, check: Callable[[], None]) -> dict:
    image, _flags, _args = _oci(cfg)
    docker = shutil.which("docker")
    if not docker:
        raise ValueError("mcp_package_docker_required")
    env = _env(DOCKER_CLI_HINTS="false")
    _run([docker, "pull", image], env=env, timeout=900, check=check)
    digests = json.loads(_run([docker, "image", "inspect", "--format", "{{json .RepoDigests}}", image], env=env, timeout=60,
                              check=check) or "[]")
    repository = re.sub(r"(:[A-Za-z0-9._-]+|@sha256:[0-9a-f]{64})$", "", image)

    def bare(name: str) -> str:
        return name.removeprefix("docker.io/library/").removeprefix("docker.io/")
    found = next((d for d in digests or [] if isinstance(d, str) and bare(d.rsplit("@", 1)[0]) == bare(repository)), "")
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", found):
        raise ValueError("mcp_package_image_unverified")
    digest = found.rsplit("@", 1)[1]
    tag = image.rsplit(":", 1)[1] if "@" not in image else ""
    return {"kind": "oci", "name": repository, "version": tag, "image": repository + "@" + digest,
            "items": [{"path": "", "name": repository, "version": tag, "integrity": digest}], "integrity": digest}


def bundle_install(upload: str, bundle) -> dict:
    """The recipe for a checked bundle: its own launch, with ``{bundle}`` for its unpacked folder and its
    settings as declared inputs. Bundles that install packages when they start (``uv``) aren't run."""
    command = bundle.command
    if bundle.server_type == "uv" or not (command in {"node", "python", "python3"} or command.startswith("{bundle}/")):
        raise ValueError("bundle_runtime_unsupported")
    return {"transport": "stdio", "command": command, "args": list(bundle.args), "env": dict(bundle.env),
            "bundle": {"upload": upload, "sha256": "sha256:" + bundle.sha256},
            **({"inputs": list(bundle.inputs)} if bundle.inputs else {}),
            **({"requirements": ["uv"]} if command.startswith("python") else {})}


def _bundle(cfg: dict):
    from row_bot.integrations import uploads
    from row_bot.mcp_client import bundles
    path = uploads.path(str(cfg["bundle"].get("upload", "")))
    if not path.is_file() or path.stat().st_size > bundles.MAX_BYTES:
        raise ValueError("mcp_bundle_unavailable")  # Picked files are kept a day; pick it again.
    data = path.read_bytes()
    bundle = bundles.read(data)
    if "sha256:" + bundle.sha256 != cfg["bundle"].get("sha256"):
        raise ValueError("mcp_package_integrity_changed")
    return bundle, data


def _mcpb_lock(cfg: dict, check: Callable[[], None]) -> dict:
    bundle, _data = _bundle(cfg)
    return {"kind": "mcpb", "name": bundle.display_name or bundle.name, "version": bundle.version, "signature": bundle.signature,
            "items": [{"path": "", "name": bundle.name, "version": bundle.version, "integrity": "sha256:" + bundle.sha256}],
            "integrity": "sha256:" + bundle.sha256, "server_type": bundle.server_type}


def review(lock: dict) -> dict:
    """What the person sees before anything is installed: every package, exact version and checksum."""
    count = len(lock["items"])
    skipped = [item["name"] for item in lock["items"] if item.get("scripts")]
    lines = {"npm": [f"{count} npm package{'s' if count != 1 else ''}, exactly these versions, checked against their checksums.",
                     "Install scripts never run."],
             "pypi": [f"{count} Python package{'s' if count != 1 else ''}, ready-built only, checked against their checksums.",
                      "Installed in a private folder for this app; nothing is installed for the whole computer."],
             "oci": ["Docker runs this image by its exact digest, with network access and no folders from this computer.",
                     "It gets only the settings you add here."],
             "mcpb": [("Signed by " + lock["signature"]["signer"] + ". Row-Bot checked the signature against the bundle, "
                       "not who the signer is.") if lock.get("signature", {}).get("status") == "signed" else
                      "Not signed, so Row-Bot can't tell who made it. Add it only if you trust where it came from.",
                      "Unpacked into a private folder and run on this computer, with the settings you add here."]}[lock["kind"]]
    if skipped:
        lines.append("These have install scripts, which Row-Bot skips, so they may not work: " + ", ".join(skipped[:8])
                     + ("…" if len(skipped) > 8 else ""))
    return {"summary": f"{lock['name']} {lock['version']}".strip()[:256], "lines": [line[:256] for line in lines][:8],
            "items": [{"name": item["name"][:214], "version": str(item["version"])[:128], "integrity": str(item["integrity"])[:128]}
                      for item in lock["items"][:200]], "digest": lock["digest"]}


# --- Install exactly the lock --------------------------------------------------------------------------

def _verified(url: str, integrity: str) -> bytes:
    if not re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", integrity):
        raise ValueError("mcp_package_integrity_required")
    raw = _fetch(url)
    if base64.b64encode(hashlib.sha512(raw).digest()).decode() != integrity.removeprefix("sha512-"):
        raise ValueError("mcp_package_integrity_changed")
    return raw


def _archive(url: str, integrity: str, root: Path) -> None:
    raw = _verified(url, integrity)
    total = count = 0
    seen = set()
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            count += 1
            total += member.size
            if count > 8192 or total > _MAX:
                raise ValueError("mcp_package_too_large")
            parts = member.name.removeprefix("./").split("/", 1)
            if len(parts) != 2 or not parts[0]:
                raise ValueError("unsafe_package_path")
            path = parts[1].rstrip("/") if member.isdir() else parts[1]
            if not path:
                continue
            relative_package_path(path)
            if path.casefold() in seen or not (member.isfile() or member.isdir()):
                raise ValueError("package_link_or_collision")
            seen.add(path.casefold())
            destination = contained_path(root, path)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                content = archive.extractfile(member)
                if content is None:
                    raise ValueError("mcp_package_invalid")
                destination.write_bytes(content.read(_MAX + 1))


def _manifest(root: Path) -> dict:
    path = contained_path(root, "package.json")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("mcp_package_too_large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if type(data) is not dict:
        raise ValueError("mcp_package_invalid")
    return data


def _site_digest(root: Path) -> str:
    """A Python install's tree without byte-code caches, which the interpreter may write while running."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return "sha256:" + digest.hexdigest()


def _tree_digest(root: Path) -> str:
    from row_bot.plugins.devtools import compute_plugin_checksum
    check_package_tree(root, **_TREE)
    return compute_plugin_checksum(root)


def inspect(owner_id: str, cfg: dict, lock: dict | None = None, *, check: Callable[[], None] = lambda: None) -> dict:
    """Lay out exactly the reviewed lock in a new private folder; never run anything from it."""
    lock = lock or resolve(cfg, check=check)
    if lock.get("kind") != kind(cfg):
        raise ValueError("mcp_package_integrity_changed")
    preview_id = uuid4().hex
    root = _folder(preview_id)
    root.mkdir(parents=True)
    try:
        if lock["kind"] == "npm":
            entry = _install_npm(lock, root)
            digest = _tree_digest(root)
        elif lock["kind"] == "pypi":
            entry = _install_pypi(lock, root, check)
            digest = _site_digest(root / "site")
        elif lock["kind"] == "mcpb":
            from row_bot.mcp_client import bundles
            bundle, data = _bundle(cfg)
            if "sha256:" + bundle.sha256 != lock["integrity"]:
                raise ValueError("mcp_package_integrity_changed")
            root.rmdir()  # The bundle unpacks into a folder that doesn't exist yet.
            digest = bundles.extract(data, root, check=check)
            entry = _bundle_python(check) if cfg.get("command", "").startswith("python") else cfg["command"]
        else:
            entry, digest = lock["image"], lock["integrity"]
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    summary = {"preview_id": preview_id, "name": lock["name"], "version": lock["version"], "integrity": lock["integrity"],
        "digest": digest, "lock_digest": lock["digest"], "dependencies": max(0, len(lock["items"]) - 1), "kind": lock["kind"],
        "license": "", "disclosures": review(lock)["lines"]}
    _PREVIEWS.put((owner_id, preview_id), {"summary": summary, "root": root, "entry": entry, "args": arguments(cfg), "cfg": cfg})
    return summary


def arguments(cfg: dict) -> list[str]:
    """The package's own arguments in a recipe (after the package or image)."""
    found = kind(cfg)
    if found == "mcpb":
        return [str(arg) for arg in cfg.get("args", [])]
    return (requirement(cfg) or ("", "", []))[2] if found == "npm" else _pypi(cfg)[2] if found == "pypi" else _oci(cfg)[2]


def _install_npm(lock: dict, root: Path) -> str:
    for item in sorted(lock["items"], key=lambda item: item["path"].count("node_modules/")):
        target = root if not item["path"] else contained_path(root, item["path"])
        if item["path"] and target.exists():
            raise ValueError("mcp_package_recipe_unsupported")  # Bundled files must not mix with the locked graph.
        _archive(item["resolved"], item["integrity"], target)
    home = root if lock["layout"] == "package" else contained_path(root, "node_modules/" + lock["name"])
    manifest = _manifest(home)
    if manifest.get("name") != lock["name"] or manifest.get("version") != lock["version"]:
        raise ValueError("mcp_package_integrity_changed")
    bins = manifest.get("bin", {})
    if isinstance(bins, str):
        bins = {lock["name"].rsplit("/", 1)[-1]: bins}
    if type(bins) is not dict or not bins:
        raise ValueError("mcp_package_recipe_unsupported")
    entry = str(bins.get(lock["name"].rsplit("/", 1)[-1]) or next(iter(bins.values()))).removeprefix("./")
    entry = entry if lock["layout"] == "package" else "node_modules/" + lock["name"] + "/" + entry
    if not contained_path(root, entry).is_file():
        raise ValueError("mcp_package_invalid")
    return entry


def _bundle_python(check: Callable[[], None]) -> str:
    """The interpreter for a Python bundle: uv's managed Python in Row-Bot's data folder, never the system's."""
    uv = _uv()
    _run([uv, "python", "install", PYTHON], env=_uv_env(), timeout=600, check=check)
    found = _run([uv, "python", "find", PYTHON], env=_uv_env(), timeout=60, check=check).strip()
    if not found or not Path(found).is_file():
        raise ValueError("mcp_package_python_unavailable")
    return found


def _install_pypi(lock: dict, root: Path, check: Callable[[], None]) -> str:
    """A private environment with only the reviewed wheels, each checked against its hash."""
    uv = _uv()
    lines = [f"{item['name']}=={item['version']} " + " ".join("--hash=" + value for value in item["hashes"]) for item in lock["items"]]
    contained_path(root, "requirements.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    venv = root / "site"
    _run([uv, "venv", str(venv), "--python", PYTHON], env=_uv_env(), timeout=600, check=check)
    _run([uv, "pip", "install", "--python", str(_venv_python(venv)), "--require-hashes", "--no-deps", "--only-binary", ":all:",
          "--index-url", PYPI_INDEX, "-r", str(root / "requirements.txt")], env=_uv_env(), timeout=900, check=check)
    entry = ("Scripts/" + lock["entry"] + ".exe") if sys.platform == "win32" else "bin/" + lock["entry"]
    if not (venv / entry).is_file():
        raise ValueError("mcp_package_entry_missing")
    return "site/" + entry


def reviewed_launch(owner_id: str, preview_id: str, cfg: dict, digest: str) -> dict:
    from row_bot.mcp_client.auth import binding
    value = _PREVIEWS.get((owner_id, preview_id))
    if not value:
        raise ValueError("mcp_package_preview_expired")
    summary = value["summary"]
    current = (summary["digest"] if summary["kind"] == "oci" else _site_digest(value["root"] / "site")
               if summary["kind"] == "pypi" else _tree_digest(value["root"]))
    if binding("", cfg) != binding("", value["cfg"]) or digest != summary["digest"] or current != digest:
        raise ValueError("mcp_package_integrity_changed")
    return {"id": preview_id, "kind": summary["kind"], "digest": digest, "binding": binding("", cfg), "entry": value["entry"],
            "args": value["args"], "package": summary["name"], "version": summary["version"], "integrity": summary["integrity"],
            "lock_digest": summary["lock_digest"]}


def bundle_root(cfg: dict) -> str:
    """The unpacked folder a bundle's own variables name as ``{bundle}`` (``PYTHONPATH={bundle}/lib``)."""
    launch = cfg.get("managed_launch") or {}
    return str(_folder(launch["id"])) if kind(cfg) == "mcpb" and re.fullmatch(r"[a-f0-9]{32}", str(launch.get("id", ""))) else ""


def resolve_launch(cfg: dict, *, args: list[str] | None = None) -> tuple[str, list[str]] | None:
    """Read-only launch gate: never acquire dependencies during a connection. ``args`` are the
    reviewed arguments with the person's declared inputs filled in."""
    from row_bot.mcp_client.auth import binding
    from row_bot.mcp_client.requirements import managed_command_path
    found = kind(cfg)
    if found is None:
        return None
    if found == "npm":
        requirement(cfg)
    launch = cfg.get("managed_launch", {})
    if (not re.fullmatch(r"[a-f0-9]{32}", str(launch.get("id", ""))) or launch.get("binding") != binding("", cfg)
            or launch.get("kind", "npm") != found):
        raise ValueError("mcp_package_preparation_required")
    extra = launch["args"] if args is None else args
    if found == "oci":
        docker = shutil.which("docker")
        if not docker or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", str(launch["entry"])):
            raise ValueError("mcp_package_docker_required")
        _image, flags, _rest = _oci(cfg)
        return docker, ["run", "-i", "--rm", "--pull=never", *flags, launch["entry"], *extra]
    root = _folder(launch["id"])
    if found == "mcpb":
        if _tree_digest(root) != launch.get("digest"):
            raise ValueError("mcp_package_integrity_changed")
        command = str(cfg.get("command", ""))
        filled = [str(arg).replace("{bundle}", str(root)) for arg in extra]
        if command == "node":
            node = managed_command_path("node", "node") or shutil.which("node")
            if not node:
                raise ValueError("mcp_package_node_required")
            return node, filled
        if command.startswith("python"):
            if not Path(str(launch["entry"])).is_file():
                raise ValueError("mcp_package_python_unavailable")
            return str(launch["entry"]), ["-s", *filled]
        return str(contained_path(root, command.removeprefix("{bundle}/"))), filled
    if found == "pypi":
        if _site_digest(root / "site") != launch.get("digest"):
            raise ValueError("mcp_package_integrity_changed")
        return str(contained_path(root, launch["entry"])), list(extra)
    if _tree_digest(root) != launch.get("digest"):
        raise ValueError("mcp_package_integrity_changed")
    node = managed_command_path("node", "node") or shutil.which("node")
    if not node:
        raise ValueError("mcp_package_node_required")
    return node, ["--no-global-search-paths", str(contained_path(root, launch["entry"])), *extra]
