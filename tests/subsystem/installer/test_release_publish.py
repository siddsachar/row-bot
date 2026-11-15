"""What the local publish step may sign and publish (`scripts/release_publish.py`).

`scripts/release_windows.ps1` asks these decisions before it signs anything
and before it publishes: which run, whether that run built the tag's current
commit, whether every file on the draft is one that run built, and whether the
draft still holds exactly the files its SHA-256 block lists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.release_publish as publish

pytestmark = [pytest.mark.subsystem, pytest.mark.installer]

VERSION = "5.2.0"
TAG_SHA = "a" * 40
INSTALLER = f"Row-Bot-{VERSION}-Windows-x64.exe"
DMG = f"Row-Bot-{VERSION}-macOS-arm64.dmg"


def _run(run_id, *, title=f"Release {VERSION}", branch=f"v{VERSION}", conclusion="success", sha=TAG_SHA):
    return {"databaseId": run_id, "displayTitle": title, "headBranch": branch, "conclusion": conclusion,
            "status": "completed", "headSha": sha}


def test_the_newest_successful_run_of_the_tag_is_published() -> None:
    runs = [_run(10), _run(30, conclusion="failure"), _run(20), _run(40, branch="main"),
            _run(50, title=f"Release {VERSION} (dry run)", branch="feat/x")]

    assert publish.select_run(runs, VERSION, dry_run=False) == "20"
    assert publish.select_run(runs, VERSION, dry_run=True) == "50"
    assert publish.select_run(runs, "5.2.1", dry_run=False) is None


@pytest.mark.parametrize(
    ("run", "problem"),
    [
        (_run(1), None),
        (_run(1, sha="b" * 40), "the tag v5.2.0 is now"),
        (_run(1, branch="feat/release-pipeline"), "not the tag v5.2.0"),
        (_run(1, conclusion="failure"), "did not succeed"),
        (_run(1, title=f"Release {VERSION} (dry run)"), "not 'Release 5.2.0'"),
    ],
)
def test_a_run_is_published_only_if_it_built_the_tags_current_commit(run, problem) -> None:
    problems = publish.run_problems(run, VERSION, dry_run=False, tag_sha=TAG_SHA)

    assert problems == [] if problem is None else any(problem in item for item in problems)


def test_a_dry_run_is_checked_by_its_name_only() -> None:
    run = _run(1, title=f"Release {VERSION} (dry run)", branch="feat/x", conclusion=None, sha="c" * 40)

    assert publish.run_problems(run, VERSION, dry_run=True, tag_sha=None) == []


def _asset(name, digest):
    return {"name": name, "size": 10, "sha256": digest}


CI = {DMG: "1" * 64, f"row-bot-code-{VERSION}.tar.xz": "2" * 64}


def test_every_file_on_the_draft_is_one_this_run_built() -> None:
    record = {"version": VERSION, "release_run_id": "77", "name": DMG, "sha256": "9" * 64}
    assets = [_asset(DMG, "1" * 64), _asset(f"row-bot-code-{VERSION}.tar.xz", "2" * 64),
              _asset(INSTALLER, "f" * 64)]

    listing, problems = publish.accept_assets(assets, version=VERSION, run_id="77", installer=INSTALLER,
                                              ci_hashes=CI, records=[record])

    assert problems == []
    # The installer on the draft is replaced by the one signed here.
    assert [item["name"] for item in listing] == [DMG, f"row-bot-code-{VERSION}.tar.xz"]


def test_a_dmg_notarize_finish_attached_is_accepted_by_its_record_for_this_run_only() -> None:
    late = [_asset(DMG, "9" * 64)]
    record = {"version": VERSION, "release_run_id": "77", "name": DMG, "sha256": "9" * 64}

    assert publish.accept_assets(late, version=VERSION, run_id="77", installer=INSTALLER,
                                 ci_hashes={}, records=[record])[1] == []
    # The record of another run (the tag moved since) vouches for nothing.
    _, problems = publish.accept_assets(late, version=VERSION, run_id="78", installer=INSTALLER,
                                        ci_hashes={}, records=[record])
    assert problems == [f"{DMG} was not built by release run 78 (no checksum or notarization record)"]


@pytest.mark.parametrize(
    ("asset", "problem"),
    [
        (_asset(DMG, "8" * 64), "is not the file release run 77 built"),
        (_asset(f"row-bot-assets-{VERSION}.tar.xz", "3" * 64), "was not built by release run 77"),
        (_asset(f"Row-Bot-{VERSION}-Windows-arm64.exe", "4" * 64), "is not a file of release 5.2.0"),
        (_asset(f"row-bot-container-{VERSION}.json", "5" * 64), "is not a file of release 5.2.0"),
    ],
)
def test_a_stale_or_foreign_file_stops_the_release(asset, problem) -> None:
    _, problems = publish.accept_assets([asset], version=VERSION, run_id="77", installer=INSTALLER,
                                        ci_hashes=CI, records=[])

    assert len(problems) == 1 and problem in problems[0]


def test_the_draft_must_still_hold_what_the_block_lists() -> None:
    listing = [_asset(DMG, "1" * 64), _asset(INSTALLER, "f" * 64)]

    assert publish.same_assets(listing, listing) == []
    assert publish.same_assets(listing, [_asset(DMG, "8" * 64), _asset(INSTALLER, "f" * 64)]) == [
        f"{DMG} changed on the draft"]
    assert publish.same_assets(listing, [*listing, _asset("extra.txt", "0" * 64)]) == ["extra.txt appeared on the draft"]
    assert publish.same_assets(listing, listing[:1]) == [f"{INSTALLER} is gone from the draft"]


def test_draft_files_take_githubs_digest_or_the_downloaded_hash(tmp_path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "row-bot-update-5.2.0.json").write_bytes(b"{}")
    release = {"assets": [{"name": DMG, "size": 3, "digest": "sha256:" + "1" * 64},
                          {"name": "row-bot-update-5.2.0.json", "size": 2, "digest": None}]}

    assert publish.missing_digests(release) == ["row-bot-update-5.2.0.json"]
    assets = publish.draft_assets(release, downloads)
    assert assets[1]["sha256"] == publish.sha256_file(downloads / "row-bot-update-5.2.0.json")
    (downloads / DMG).write_bytes(b"not the dmg")
    with pytest.raises(ValueError, match="does not match GitHub's digest"):
        publish.draft_assets(release, downloads)


def test_the_command_line_writes_the_listing_with_the_signed_installer(tmp_path) -> None:
    installer = tmp_path / INSTALLER
    installer.write_bytes(b"signed installer")
    (tmp_path / "assets.json").write_text(json.dumps([_asset(DMG, "1" * 64)]), encoding="utf-8-sig")
    (tmp_path / "ci.txt").write_text(f"{'1' * 64}  {DMG}\n", encoding="utf-8")
    command = ["accept-assets", "--assets", str(tmp_path / "assets.json"), "--version", VERSION, "--run-id", "77",
               "--installer", INSTALLER, "--installer-file", str(installer), "--ci-hashes", str(tmp_path / "ci.txt"),
               "--out", str(tmp_path / "listing.json")]

    assert publish.main(command) == 0
    listing = json.loads(Path(tmp_path / "listing.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in listing] == [DMG, INSTALLER]
    assert listing[1]["sha256"] == publish.sha256_file(installer)


CONTAINER = f"row-bot-container-{VERSION}.json"
OLD, NEW = "a" * 64, "b" * 64


def _published(block, *, draft=False):
    lines = "\n".join(f"  {name}: sha256={digest}" for name, digest in block.items())
    body = f"Notes.\n\n<!-- row-bot-update-manifest -->\n```manifest\nschema: 1\nfiles:\n{lines}\n```\n"
    return {"draft": draft, "body": body}


def _resign(release, assets, **kwargs):
    return publish.resign_listing(release, assets, installer=INSTALLER, installer_sha256=NEW, installer_size=99,
                                  **kwargs)


def test_resign_replaces_only_the_installer_line_of_a_published_release() -> None:
    block = {DMG: "1" * 64, INSTALLER: OLD, CONTAINER: "5" * 64}
    release = _published(block)
    assets = [_asset(DMG, "1" * 64), _asset(INSTALLER, OLD), _asset(CONTAINER, "5" * 64)]

    listing, problems = _resign(release, assets)

    assert problems == []
    # The container line container.yml added after publishing is kept.
    assert {item["name"]: item["sha256"] for item in listing} == {**block, INSTALLER: NEW}
    # Not finished until the release holds and lists the new installer.
    assert _resign(release, assets, done=True)[1] == [
        f"the release does not list and hold the {INSTALLER} signed here"]
    # A run that stopped after the upload carries on.
    uploaded = [_asset(DMG, "1" * 64), _asset(INSTALLER, NEW), _asset(CONTAINER, "5" * 64)]
    assert _resign(release, uploaded)[1] == []
    assert _resign(_published({**block, INSTALLER: NEW}), uploaded, done=True)[1] == []


@pytest.mark.parametrize(
    ("release", "assets", "problem"),
    [
        (_published({DMG: "1" * 64, INSTALLER: OLD}, draft=True), [_asset(DMG, "1" * 64), _asset(INSTALLER, OLD)],
         "the release is a draft"),
        ({"draft": False, "body": "Notes."}, [_asset(INSTALLER, OLD)], "the release has no SHA-256 block"),
        (_published({DMG: "1" * 64, INSTALLER: OLD}), [_asset(DMG, "8" * 64), _asset(INSTALLER, OLD)],
         f"{DMG} changed since the release was published"),
        (_published({DMG: "1" * 64, INSTALLER: OLD}), [_asset(INSTALLER, OLD)],
         f"{DMG} is in the block but gone from the release"),
        (_published({INSTALLER: OLD}), [_asset(INSTALLER, OLD), _asset("extra.txt", "0" * 64)],
         "extra.txt is on the release but not in its block"),
        (_published({INSTALLER: OLD}), [_asset(INSTALLER, "c" * 64)], "neither the published one nor"),
        (_published({DMG: "1" * 64}), [_asset(DMG, "1" * 64)], f"the block does not list {INSTALLER}"),
    ],
)
def test_resign_refuses_a_release_that_changed_or_is_not_published(release, assets, problem) -> None:
    _, problems = _resign(release, assets)

    assert any(problem in item for item in problems), problems


def _certutil(leaf_status: str) -> str:
    # The shape of `certutil -verify -urlfetch` for a code signing leaf and its issuer (trimmed).
    return (
        "CertContext[0][0]: dwInfoStatus=102 dwErrorStatus=0\n"
        '  Verified "Base CRL (1d096e)" Time: 0 48132dc1\n'
        "    [0.0] http://ccsca2021.crl.certum.pl/ccsca2021.crl\n"
        "  ----------------  Certificate OCSP  ----------------\n"
        f'  {leaf_status} "OCSP" Time: 0 fd494ea9\n'
        "    [0.0] http://ccsca2021.ocsp-certum.com\n"
        "CertContext[0][1]: dwInfoStatus=102 dwErrorStatus=0\n"
        "  ----------------  Certificate OCSP  ----------------\n"
        '  Verified "OCSP" Time: 0 4a212569\n'
    )


def test_a_revoked_certificate_never_signs_even_while_the_cached_crl_says_valid() -> None:
    assert publish.revocation_problems(_certutil("Verified")) == []
    assert "is revoked" in publish.revocation_problems(_certutil("Revoked"))[0]
    # No fresh OCSP answer for the leaf (the issuer's does not count): refused, not assumed fine.
    assert "did not confirm" in publish.revocation_problems(_certutil("Failed"))[0]
    assert publish.revocation_problems("CertUtil: -verify command FAILED") == [
        "certutil printed no chain for the certificate"]
