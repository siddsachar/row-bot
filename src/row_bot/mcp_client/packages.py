"""Nonexecuting preparation of bounded, integrity-pinned npm MCP packages.

Only self-contained archives or complete npm shrinkwraps are supported. No npm
process, install script, dependency solver or global installation is invoked.
The managed Node runtime remains owned by requirements.py.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
from urllib.parse import quote
from uuid import uuid4

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.integrations.safe import TtlCache, fetch
from row_bot.package_files import check_package_tree, contained_path, relative_package_path

_NAME = r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+"
_PREVIEWS = TtlCache(1200, 32, full="mcp_package_preview_capacity")
_MAX = 64 * 1024 * 1024


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


def _fetch(url: str, *, maximum: int = _MAX) -> bytes:
    return fetch(url, hosts={"registry.npmjs.org"}, max_bytes=maximum, timeout=30,
        refused="mcp_package_source_invalid", too_large="mcp_package_too_large")


def _archive(url: str, integrity: str, root: Path) -> None:
    if not re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", integrity):
        raise ValueError("mcp_package_integrity_required")
    raw = _fetch(url)
    if base64.b64encode(hashlib.sha512(raw).digest()).decode() != integrity.removeprefix("sha512-"):
        raise ValueError("mcp_package_integrity_changed")
    total = count = 0
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            count += 1
            total += member.size
            if count > 8192 or total > _MAX:
                raise ValueError("mcp_package_too_large")
            parts = member.name.removeprefix("./").split("/", 1)
            if len(parts) != 2 or parts[0] != "package":
                raise ValueError("unsafe_package_path")
            path = parts[1].rstrip("/") if member.isdir() else parts[1]
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
    check_package_tree(root)


def _manifest(root: Path) -> dict:
    path = contained_path(root, "package.json")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("mcp_package_too_large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if type(data) is not dict:
        raise ValueError("mcp_package_invalid")
    if any(key in data.get("scripts", {}) for key in ("preinstall", "install", "postinstall")):
        raise ValueError("mcp_package_install_scripts_unsupported")
    return data


def inspect(owner_id: str, cfg: dict) -> dict:
    """Explicit public lookup and staging; never execute downloaded content."""
    package = requirement(cfg)
    if package is None:
        raise ValueError("mcp_package_recipe_unsupported")
    name, version, args = package
    metadata = json.loads(_fetch("https://registry.npmjs.org/" + quote(name, safe="@") + "/" + quote(version, safe=""), maximum=4 * 1024 * 1024))
    if metadata.get("name") != name or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", metadata.get("version", "")):
        raise ValueError("mcp_package_invalid")
    preview_id = uuid4().hex
    root = contained_path(get_row_bot_data_dir(create=False), "mcp_packages/" + preview_id)
    root.mkdir(parents=True)
    distribution = metadata.get("dist", {})
    _archive(distribution.get("tarball", ""), distribution.get("integrity", ""), root)
    package_data = _manifest(root)
    if package_data.get("name") != name or package_data.get("version") != metadata["version"]:
        raise ValueError("mcp_package_integrity_changed")
    dependencies = []
    if package_data.get("dependencies") or package_data.get("optionalDependencies") or package_data.get("peerDependencies"):
        lock_path = contained_path(root, "npm-shrinkwrap.json")
        if not lock_path.is_file() or lock_path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("mcp_package_locked_dependencies_required")
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        packages = lock.get("packages", {})
        if lock.get("lockfileVersion") not in {2, 3} or type(packages) is not dict or not 1 <= len(packages) <= 128:
            raise ValueError("mcp_package_locked_dependencies_required")
        for relative, value in packages.items():
            if not relative:
                continue
            if not relative.startswith("node_modules/") or value.get("link") or value.get("hasInstallScript"):
                raise ValueError("mcp_package_recipe_unsupported")
            relative_package_path(relative)
            child = contained_path(root, relative)
            # Existing bundled dependencies must not be mixed with a new graph.
            if child.exists():
                raise ValueError("mcp_package_recipe_unsupported")
            _archive(value.get("resolved", ""), value.get("integrity", ""), child)
            _manifest(child)
            dependencies.append({"path": relative, "version": value.get("version", ""), "integrity": value["integrity"]})
        # Node's own lookup can resolve parent dependencies. Refuse missing
        # declarations rather than falling through to ambient node_modules.
        for directory in [root, *[contained_path(root, item["path"]) for item in dependencies]]:
            declared = _manifest(directory)
            for dependency in {**declared.get("dependencies", {}), **declared.get("peerDependencies", {})}:
                if not re.fullmatch(_NAME, dependency):
                    raise ValueError("mcp_package_invalid")
                current = directory
                while not (current / "node_modules" / dependency / "package.json").is_file():
                    if current == root:
                        raise ValueError("mcp_package_locked_dependencies_required")
                    current = current.parent
    bins = package_data.get("bin", {})
    if isinstance(bins, str):
        bins = {name.rsplit("/", 1)[-1]: bins}
    if type(bins) is not dict or len(bins) != 1:
        raise ValueError("mcp_package_recipe_unsupported")
    entry = next(iter(bins.values())).removeprefix("./")
    if not contained_path(root, entry).is_file():
        raise ValueError("mcp_package_invalid")
    check_package_tree(root)
    from row_bot.plugins.devtools import compute_plugin_checksum
    digest = compute_plugin_checksum(root)
    summary = {"preview_id": preview_id, "name": name, "version": metadata["version"], "integrity": distribution["integrity"],
        "digest": digest, "dependencies": len(dependencies), "license": str(package_data.get("license", ""))[:256],
        "disclosures": ["Download from the public npm registry; integrity pins the reviewed package and dependency bytes.",
            "No install scripts run. Launch uses the separately approved Node runtime with a private package directory.",
            "Package execution and any data or telemetry it sends require your review of its publisher documentation."]}
    _PREVIEWS.put((owner_id, preview_id), {"summary": summary, "root": root, "entry": entry, "args": args, "cfg": cfg})
    return summary


def reviewed_launch(owner_id: str, preview_id: str, cfg: dict, digest: str) -> dict:
    from row_bot.plugins.devtools import compute_plugin_checksum
    from row_bot.mcp_client.auth import binding
    value = _PREVIEWS.get((owner_id, preview_id))
    if not value:
        raise ValueError("mcp_package_preview_expired")
    check_package_tree(value["root"])
    if (binding("", cfg) != binding("", value["cfg"]) or digest != value["summary"]["digest"]
            or compute_plugin_checksum(value["root"]) != digest):
        raise ValueError("mcp_package_integrity_changed")
    return {"id": preview_id, "digest": digest, "binding": binding("", cfg), "entry": value["entry"],
        "args": value["args"], "package": value["summary"]["name"], "version": value["summary"]["version"],
        "integrity": value["summary"]["integrity"]}


def resolve_launch(cfg: dict) -> tuple[str, list[str]] | None:
    """Read-only launch gate: never acquire dependencies during a connection."""
    from row_bot.mcp_client.auth import binding
    from row_bot.mcp_client.requirements import managed_command_path
    from row_bot.plugins.devtools import compute_plugin_checksum
    import shutil
    package = requirement(cfg)
    if package is None:
        return None
    launch = cfg.get("managed_launch", {})
    if not re.fullmatch(r"[a-f0-9]{32}", str(launch.get("id", ""))) or launch.get("binding") != binding("", cfg):
        raise ValueError("mcp_package_preparation_required")
    root = contained_path(get_row_bot_data_dir(create=False), "mcp_packages/" + launch["id"])
    check_package_tree(root)
    if compute_plugin_checksum(root) != launch.get("digest"):
        raise ValueError("mcp_package_integrity_changed")
    node = managed_command_path("node", "node") or shutil.which("node")
    if not node:
        raise ValueError("mcp_package_node_required")
    return node, ["--no-global-search-paths", str(contained_path(root, launch["entry"])), *launch["args"]]
