"""Bounded nonexecuting differences for the existing package lifecycle review."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from row_bot.package_files import check_package_tree
from row_bot.plugins.manifest import parse_manifest
from row_bot.mcp_client.packages import requirement
from row_bot.mcp_client.requirements import requirements_for_server


def describe_package_changes(previous: Path | None, candidate: Path, *, source_identity: str) -> list[str]:
    """Name changed owned files and declarations without exposing credential values."""
    def files(root: Path | None) -> dict[str, str]:
        if root is None or not root.is_dir():
            return {}
        check_package_tree(root)
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file() and not any(part in {".git", "__pycache__"} for part in p.relative_to(root).parts)}
    before, after = files(previous), files(candidate)
    lines = [f"File {('added' if name not in before else 'removed' if name not in after else 'changed')}: {name}"
             for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
    old = parse_manifest(previous, source_identity=source_identity) if previous and previous.is_dir() else None
    new = parse_manifest(candidate, source_identity=source_identity)
    for title, current, proposed in [
        ("Permissions", old.permissions if old else [], new.permissions),
        ("Protected credential fields", sorted(old.secrets) if old else [], sorted(new.secrets)),
        ("Account authorization declarations", old.auth if old else {}, new.auth),
    ]:
        if current != proposed:
            if title == "Account authorization declarations":
                lines.append("Account authorization declarations changed. Existing upstream grants do not expand automatically; review the publisher's authorization screen on next sign-in.")
            else:
                lines.append(f"{title}: {', '.join(current) or 'none'} → {', '.join(proposed) or 'none'}")
    for kind in ("native_tools", "skills", "channels", "mcp_servers"):
        current = getattr(old.provides, kind) if old else []
        proposed = getattr(new.provides, kind)
        if current == proposed:
            continue
        def named(values):
            return {str(value.get("id") or value.get("name") or index): value for index, value in enumerate(values)}
        existing, incoming = named(current), named(proposed)
        for name in sorted(existing.keys() | incoming.keys()):
            if existing.get(name) == incoming.get(name):
                continue
            lines.append(f"{kind} {('added' if name not in existing else 'removed' if name not in incoming else 'changed')}: {name}")
            if kind == "mcp_servers":
                for label, value in (("Previous", existing.get(name)), ("Proposed", incoming.get(name))):
                    if value is None:
                        continue
                    url = urlsplit(str(value.get("url") or ""))
                    destination = f"{url.scheme}://{url.hostname or ''}{url.path}" if url.scheme else "local process"
                    lines.append(f"{label} destination: {destination}; executable: {value.get('command') or 'hosted service'}")
                    runtime_names = [runtime.id for runtime in requirements_for_server(value)]
                    lines.append(f"{label} runtime dependencies: {', '.join(runtime_names) or 'none declared'}")
                    try:
                        dependency = requirement(value)
                        detail = f"{dependency[0]}@{dependency[1]} (transitive dependencies require package preparation review)" if dependency else "no package-manager dependency declared"
                    except ValueError:
                        detail = "unsupported executable recipe; inspect the source before preparation"
                    lines.append(f"{label} executable package: {detail}")
                    lines.append(f"{label} execution/dependency declaration digest: {hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()}. Changed runtime declarations require preparation and tool review.")
    return lines or ["No package file or capability changes."]
