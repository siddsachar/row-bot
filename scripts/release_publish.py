"""The decisions of the local publish step (``scripts/release_windows.ps1``), testable.

The PowerShell script does the talking (gh, signtool, ISCC); every decision
about what it may sign and publish is made here:

``select-run``          the release run to publish: "Release X.Y.Z" from the tag ``vX.Y.Z``
``check-run``           a run may be published: right name, built the tag's current commit, succeeded
``missing-digests``     the draft's files GitHub gave no SHA-256 for (they are downloaded and hashed)
``draft-assets``        the draft's files as ``[{name, size, sha256}]``
``folder-assets``       the same for a folder (dry runs read the run's artifacts)
``accept-assets``       every file on the draft was built by this run: CI's checksum or, for a DMG
                        notarize-finish.yml attached later, its record for this run
``same-assets``         the draft still holds exactly the files the SHA-256 block lists
``resign-listing``      a published release whose Windows installer is re-signed (``-Resign``): every
                        other file is still the one its block lists, and the block with the new installer
``check-revocation``    the signing certificate is not revoked, by a fresh OCSP answer
                        (``certutil -verify -urlfetch``; Windows' cached CRL can be a week old)

Stdlib only; run it with any Python 3.11+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
import append_sha_manifest as manifest  # noqa: E402
import build_update_parts as parts  # noqa: E402

_SHA = re.compile(r"[0-9a-f]{64}")


def title(version: str, dry_run: bool) -> str:
    return f"Release {version}" + (" (dry run)" if dry_run else "")


def select_run(runs: Iterable[Mapping[str, Any]], version: str, *, dry_run: bool) -> str | None:
    """The newest successful release.yml run that built *version* (from its tag)."""
    wanted = title(version, dry_run)
    found = [
        run for run in runs
        if run.get("displayTitle") == wanted and run.get("conclusion") == "success"
        and (dry_run or run.get("headBranch") == f"v{version}")
    ]
    if not found:
        return None
    return str(max(found, key=lambda run: int(run.get("databaseId") or 0))["databaseId"])


def run_problems(run: Mapping[str, Any], version: str, *, dry_run: bool, tag_sha: str | None) -> list[str]:
    """Why a run's files may not be signed and published as *version*."""
    problems = []
    if run.get("displayTitle") != title(version, dry_run):
        problems.append(f"the run is '{run.get('displayTitle')}', not '{title(version, dry_run)}'")
    if dry_run:
        return problems
    if run.get("headBranch") != f"v{version}":
        problems.append(f"the run built {run.get('headBranch')}, not the tag v{version}")
    if (run.get("status"), run.get("conclusion")) != ("completed", "success"):
        problems.append(f"the run did not succeed ({run.get('status')} {run.get('conclusion')})")
    if not tag_sha or run.get("headSha") != tag_sha:
        problems.append(f"the run built {run.get('headSha')}, but the tag v{version} is now {tag_sha}: "
                        "run the release again from the tag")
    return problems


def _digest(asset: Mapping[str, Any]) -> str | None:
    match = re.fullmatch(r"sha256:([0-9a-f]{64})", str(asset.get("digest") or ""))
    return match.group(1) if match else None


def missing_digests(release: Mapping[str, Any]) -> list[str]:
    return [str(asset["name"]) for asset in release.get("assets") or [] if _digest(asset) is None]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def draft_assets(release: Mapping[str, Any], downloads: Path | None = None) -> list[dict[str, Any]]:
    """Name, size and SHA-256 of each draft file: GitHub's digest, or the downloaded file's hash."""
    out = []
    for asset in release.get("assets") or []:
        name = str(asset["name"])
        digest = _digest(asset)
        local = downloads / name if downloads else None
        if local is not None and local.is_file():
            hashed = sha256_file(local)
            if digest and digest != hashed:
                raise ValueError(f"{name}: the download does not match GitHub's digest")
            digest = hashed
        if digest is None:
            raise ValueError(f"{name}: no digest and not downloaded")
        out.append({"name": name, "size": int(asset.get("size") or 0), "sha256": digest})
    return out


def folder_assets(folder: Path) -> list[dict[str, Any]]:
    return [{"name": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)}
            for path in sorted(folder.iterdir()) if path.is_file()]


def parse_ci_hashes(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        match = re.match(r"^([0-9a-f]{64})\s+\*?(.+)$", line.strip())
        if match:
            out[match.group(2).strip()] = match.group(1)
    return out


def accept_assets(
    assets: Iterable[Mapping[str, Any]], *, version: str, run_id: str, installer: str,
    ci_hashes: Mapping[str, str], records: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[str]]:
    """The draft's files this run built (without the installer built here), and what is wrong.

    A file is this run's when its SHA-256 is the one CI recorded for the run,
    or, for a DMG notarize-finish.yml attached later, the one its record for
    this run names. Anything else (a file of an earlier run, a stray upload, a
    name no release carries) stops the release.
    """
    allowed = set(parts.release_file_names(version))
    recorded = {
        str(record.get("name")): str(record.get("sha256"))
        for record in records
        if str(record.get("release_run_id")) == str(run_id) and record.get("version") == version
    }
    listing: list[dict[str, Any]] = []
    problems: list[str] = []
    for asset in assets:
        name, digest = str(asset["name"]), str(asset["sha256"]).lower()
        if name == installer:
            continue  # replaced by the installer signed here
        if name not in allowed:
            problems.append(f"{name} is not a file of release {version}: remove it from the draft")
            continue
        expected = ci_hashes.get(name) or recorded.get(name)
        if expected is None:
            problems.append(f"{name} was not built by release run {run_id} (no checksum or notarization record)")
        elif expected != digest:
            problems.append(f"{name} on the draft is not the file release run {run_id} built")
        else:
            listing.append({"name": name, "size": int(asset["size"]), "sha256": digest})
    return listing, problems


def same_assets(expected: Iterable[Mapping[str, Any]], current: Iterable[Mapping[str, Any]]) -> list[str]:
    """Differences between the files the block lists and the files on the draft now."""
    want = {str(item["name"]): str(item["sha256"]).lower() for item in expected}
    have = {str(item["name"]): str(item["sha256"]).lower() for item in current}
    problems = [f"{name} is gone from the draft" for name in sorted(set(want) - set(have))]
    problems += [f"{name} appeared on the draft" for name in sorted(set(have) - set(want))]
    problems += [f"{name} changed on the draft" for name in sorted(set(want) & set(have)) if want[name] != have[name]]
    return problems


def resign_listing(
    release: Mapping[str, Any], assets: Iterable[Mapping[str, Any]], *, installer: str, installer_sha256: str,
    installer_size: int, done: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """The block of a published release with its Windows installer re-signed, and what is wrong.

    Only the installer may differ from the block: on the release it is the
    published one or (a run that stopped after the upload) the one signed
    here. With *done*, the block and the release must both hold the new one.
    """
    problems = []
    if release.get("draft"):
        problems.append("the release is a draft: publish it with a normal run, not -Resign")
    block = manifest.parse_block(str(release.get("body") or ""))
    if not block:
        return [], problems + ["the release has no SHA-256 block"]
    have = {str(item["name"]): str(item["sha256"]).lower() for item in assets}
    sizes = {str(item["name"]): int(item["size"]) for item in assets}
    new = installer_sha256.lower()
    if installer not in block:
        problems.append(f"the block does not list {installer}")
    elif installer not in have:
        problems.append(f"{installer} is gone from the release")
    elif done and (block[installer], have[installer]) != (new, new):
        problems.append(f"the release does not list and hold the {installer} signed here")
    elif have[installer] not in {block[installer], new}:
        problems.append(f"{installer} on the release is neither the published one nor the one signed here")
    for name in sorted((set(block) | set(have)) - {installer}):
        if name not in have:
            problems.append(f"{name} is in the block but gone from the release")
        elif name not in block:
            problems.append(f"{name} is on the release but not in its block")
        elif block[name] != have[name]:
            problems.append(f"{name} changed since the release was published")
    listing = [
        {"name": name, "size": installer_size if name == installer else sizes.get(name, 0),
         "sha256": new if name == installer else digest}
        for name, digest in sorted(block.items())
    ]
    return listing, problems


def revocation_problems(certutil_output: str) -> list[str]:
    """Why the signing certificate may not sign, from ``certutil -verify -urlfetch`` of it.

    Only the leaf (``CertContext[0][0]``) counts. certutil exits 0 even for a
    revoked certificate when its cached CRL is older than the revocation, so
    the answer is the leaf's OCSP line: ``Verified "OCSP"`` and nothing revoked.
    """
    leaf = re.search(r"^CertContext\[0\]\[0\].*?(?=^CertContext\[|\Z)", certutil_output, re.M | re.S)
    if not leaf:
        return ["certutil printed no chain for the certificate"]
    if re.search(r'^\s*Revoked\s+"', leaf.group(0), re.M):
        return ["the certificate is revoked: sign with the certificate Certum issued in its place (-CertificateSha1)"]
    if not re.search(r'^\s*Verified\s+"OCSP"', leaf.group(0), re.M):
        return ["Certum's OCSP server did not confirm the certificate (offline?); try again"]
    return []


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write(path: Path, value: Any) -> None:
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    pick = commands.add_parser("select-run")
    pick.add_argument("--runs", type=Path, required=True)
    pick.add_argument("--version", required=True)
    pick.add_argument("--dry-run", action="store_true")
    checked = commands.add_parser("check-run")
    checked.add_argument("--run", type=Path, required=True)
    checked.add_argument("--version", required=True)
    checked.add_argument("--dry-run", action="store_true")
    checked.add_argument("--tag-sha")
    missing = commands.add_parser("missing-digests")
    missing.add_argument("--release", type=Path, required=True)
    draft = commands.add_parser("draft-assets")
    draft.add_argument("--release", type=Path, required=True)
    draft.add_argument("--downloads", type=Path)
    draft.add_argument("--out", type=Path, required=True)
    folder = commands.add_parser("folder-assets")
    folder.add_argument("--dir", type=Path, required=True)
    folder.add_argument("--out", type=Path, required=True)
    accept = commands.add_parser("accept-assets")
    accept.add_argument("--assets", type=Path, required=True)
    accept.add_argument("--version", required=True)
    accept.add_argument("--run-id", required=True)
    accept.add_argument("--installer", required=True)
    accept.add_argument("--ci-hashes", type=Path, required=True)
    accept.add_argument("--record", type=Path, action="append", default=[])
    accept.add_argument("--installer-file", type=Path, help="the installer signed here, added to the listing")
    accept.add_argument("--out", type=Path, required=True)
    same = commands.add_parser("same-assets")
    same.add_argument("--listing", type=Path, required=True)
    same.add_argument("--assets", type=Path, required=True)
    resign = commands.add_parser("resign-listing")
    resign.add_argument("--release", type=Path, required=True)
    resign.add_argument("--assets", type=Path, required=True)
    resign.add_argument("--installer-file", type=Path, required=True)
    resign.add_argument("--done", action="store_true", help="the release must already hold and list it")
    resign.add_argument("--out", type=Path)
    revoked = commands.add_parser("check-revocation")
    revoked.add_argument("--certutil-output", type=Path, required=True)
    args = parser.parse_args(argv)

    problems: list[str] = []
    try:
        if args.command == "select-run":
            found = select_run(_load(args.runs), args.version, dry_run=args.dry_run)
            if found is None:
                print(f"::error::no successful release.yml run '{title(args.version, args.dry_run)}'"
                      + ("" if args.dry_run else f" from v{args.version}"))
                return 1
            print(found)
            return 0
        if args.command == "check-run":
            problems = run_problems(_load(args.run), args.version, dry_run=args.dry_run, tag_sha=args.tag_sha)
        elif args.command == "missing-digests":
            print("\n".join(missing_digests(_load(args.release))))
            return 0
        elif args.command == "draft-assets":
            _write(args.out, draft_assets(_load(args.release), args.downloads))
            return 0
        elif args.command == "folder-assets":
            _write(args.out, folder_assets(args.dir))
            return 0
        elif args.command == "accept-assets":
            listing, problems = accept_assets(
                _load(args.assets), version=args.version, run_id=args.run_id, installer=args.installer,
                ci_hashes=parse_ci_hashes(args.ci_hashes.read_text(encoding="utf-8")),
                records=[_load(path) for path in args.record],
            )
            if args.installer_file:
                listing.append({"name": args.installer_file.name, "size": args.installer_file.stat().st_size,
                                "sha256": sha256_file(args.installer_file)})
            _write(args.out, listing)
        elif args.command == "same-assets":
            problems = same_assets(_load(args.listing), _load(args.assets))
        elif args.command == "resign-listing":
            listing, problems = resign_listing(
                _load(args.release), _load(args.assets), installer=args.installer_file.name,
                installer_sha256=sha256_file(args.installer_file), installer_size=args.installer_file.stat().st_size,
                done=args.done,
            )
            if args.out:
                _write(args.out, listing)
        elif args.command == "check-revocation":
            problems = revocation_problems(args.certutil_output.read_text(encoding="utf-8-sig", errors="replace"))
    except (ValueError, KeyError, OSError) as exc:
        problems = [str(exc)]
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
