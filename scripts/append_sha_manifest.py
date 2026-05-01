"""Append a SHA256 manifest to a GitHub release body.

Used in the release workflow after the Windows + macOS artifacts have
been uploaded. The manifest is the source of truth used by Thoth's
in-app updater to verify downloads.

Inputs (env vars / CLI):
    --tag           release tag, e.g. v3.19.0
    --repo          owner/repo, e.g. siddsachar/Thoth
    --token         GitHub token with `contents: write` (default: $GITHUB_TOKEN)
    --files         space- or comma-separated paths to artifacts to hash
                    (default: globbed from $RUNNER_TEMP / cwd)

Behaviour:
    1. Compute SHA256 for each artifact.
    2. Fetch the existing release body via the GitHub API.
    3. Replace any existing `<!-- thoth-update-manifest -->` block with a
       freshly-rebuilt manifest, OR append one if none is present.
    4. PATCH the release.

Idempotent: running twice produces the same body.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

_MANIFEST_RE = re.compile(
    r"<!--\s*thoth-update-manifest\s*-->\s*```manifest.*?```",
    re.DOTALL | re.IGNORECASE,
)


def sha256_of(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest_block(file_hashes: dict[str, str]) -> str:
    lines = ["<!-- thoth-update-manifest -->", "```manifest", "schema: 1", "files:"]
    for name in sorted(file_hashes):
        lines.append(f"  {name}: sha256={file_hashes[name]}")
    lines.append("```")
    return "\n".join(lines)


def merge_into_body(body: str, manifest_block: str) -> str:
    if _MANIFEST_RE.search(body or ""):
        return _MANIFEST_RE.sub(manifest_block, body)
    if not body:
        return manifest_block + "\n"
    return body.rstrip() + "\n\n" + manifest_block + "\n"


def _api(url: str, token: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "thoth-release-manifest",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--files", nargs="+", required=True,
                        help="Artifact paths to include in the manifest")
    args = parser.parse_args()

    if not args.repo:
        print("error: --repo or GITHUB_REPOSITORY required", file=sys.stderr)
        return 2
    if not args.token:
        print("error: --token or GITHUB_TOKEN required", file=sys.stderr)
        return 2

    # Compute hashes
    file_hashes: dict[str, str] = {}
    for raw in args.files:
        # Allow comma-separated list in a single arg (handy for some shells)
        for part in str(raw).replace(",", " ").split():
            p = pathlib.Path(part).expanduser().resolve()
            if not p.exists():
                print(f"warning: skipping missing file {p}", file=sys.stderr)
                continue
            file_hashes[p.name] = sha256_of(p)
            print(f"{p.name}: sha256={file_hashes[p.name]}")
    if not file_hashes:
        print("error: no files to hash", file=sys.stderr)
        return 2

    manifest_block = build_manifest_block(file_hashes)

    # Fetch release
    tag = args.tag.lstrip("v")
    api_base = f"https://api.github.com/repos/{args.repo}"
    release = _api(f"{api_base}/releases/tags/v{tag}", args.token)
    new_body = merge_into_body(release.get("body") or "", manifest_block)
    if new_body == (release.get("body") or ""):
        print("manifest unchanged — skipping PATCH")
        return 0

    _api(
        f"{api_base}/releases/{release['id']}",
        args.token,
        method="PATCH",
        payload={"body": new_body},
    )
    print(f"Manifest appended to release v{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
