"""The pinned, permissively licensed inputs that train "Hey Row-Bot" (see docs/VOICE_ENGINE.md).

Nothing here runs in Row-Bot. ``python scripts/wake_word/sources.py <folder> --yes`` downloads
what is missing into ``<folder>`` (keep it on a drive with room: about 5 GB), checks each file's
size and, once recorded, its SHA-256, and unpacks the archives. Without ``--yes`` it only lists
what it would fetch. Common Voice, MUSAN, ACAV100M, AudioSet, FMA, the MIT room recordings,
DEMAND, ESC-50, WHAM!, GTZAN and Treble10 are deliberately absent (terms to accept, size,
non-commercial or unknown licences).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sys
import tarfile
import urllib.request


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    size: int
    licence: str
    sha256: str = ""


_KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_LIBRISPEECH = "https://www.openslr.org/resources/12"
_SNSD = "https://raw.githubusercontent.com/microsoft/MS-SNSD/master"

SOURCES = (
    Source("kokoro-v1.0.fp16.onnx", f"{_KOKORO}/kokoro-v1.0.fp16.onnx", 177_464_787, "Apache-2.0"),
    Source("voices-v1.0.bin", f"{_KOKORO}/voices-v1.0.bin", 28_214_398, "Apache-2.0"),
    Source("encoders.tar.gz",
           "https://github.com/livekit/livekit-wakeword/releases/download/v2.0.0/encoders.tar.gz",
           37_490_513, "Apache-2.0",
           "a13a83c34752f6e1c20c47f1b048c02217f6d147ee721ea101eb58e59d4922e7"),
    Source("dev-clean.tar.gz", f"{_LIBRISPEECH}/dev-clean.tar.gz", 337_926_286, "CC BY 4.0"),
    Source("dev-other.tar.gz", f"{_LIBRISPEECH}/dev-other.tar.gz", 314_305_928, "CC BY 4.0"),
    Source("test-clean.tar.gz", f"{_LIBRISPEECH}/test-clean.tar.gz", 346_663_984, "CC BY 4.0"),
    Source("test-other.tar.gz", f"{_LIBRISPEECH}/test-other.tar.gz", 328_757_843, "CC BY 4.0"),
)

# MS-SNSD's noise clips from Freesound (CC0). Its one-file environments (Bus, Cafe, Kitchen,
# Office, Traffic and the like) are DEMAND's recordings (CC BY-SA 3.0) and are not used.
SNSD_CC0 = ("AirConditioner", "AirportAnnouncements", "Babble", "CopyMachine", "Munching",
            "NeighborSpeaking", "Neighbor", "ShuttingDoor", "SqueakyChair", "Typing",
            "VacuumCleaner", "WasherDryer")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _get(url: str, dest: Path) -> None:
    part = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "row-bot-wake-word-training"})
    with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as out:
        while block := response.read(1 << 20):
            out.write(block)
    part.replace(dest)


def snsd_files() -> list[tuple[str, str]]:
    """(folder, file) of every CC0 MS-SNSD noise clip, from the repository's listing."""
    import json

    out = []
    for folder in ("noise_train", "noise_test"):
        url = f"https://api.github.com/repos/microsoft/MS-SNSD/contents/{folder}?ref=master"
        request = urllib.request.Request(url, headers={"User-Agent": "row-bot-wake-word-training"})
        with urllib.request.urlopen(request, timeout=60) as response:
            listing = json.load(response)
        for item in listing:
            name = item["name"]
            stem = name.removesuffix(".wav").rstrip("0123456789").rstrip("_")
            if name.endswith(".wav") and stem in SNSD_CC0:
                out.append((folder, name))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--yes", action="store_true", help="download (the person agreed)")
    parser.add_argument("--noise", action="store_true", help="also MS-SNSD's CC0 noise clips")
    args = parser.parse_args()
    root: Path = args.folder
    root.mkdir(parents=True, exist_ok=True)
    total = sum(source.size for source in SOURCES)
    print(f"{len(SOURCES)} files, {total / 1e9:.2f} GB, into {root}")
    for source in SOURCES:
        dest = root / source.name
        print(f"  {source.name}  {source.size / 1e6:.1f} MB  {source.licence}  {source.url}")
        if not args.yes or dest.exists():
            continue
        _get(source.url, dest)
        if dest.stat().st_size != source.size:
            raise SystemExit(f"{source.name}: unexpected size {dest.stat().st_size}")
        digest = _sha256(dest)
        if source.sha256 and digest != source.sha256:
            raise SystemExit(f"{source.name}: SHA-256 {digest} is not the pinned one")
        print(f"    sha256 {digest}")
    if args.yes:
        for source in SOURCES:
            if source.name.endswith(".tar.gz"):
                marker = root / (source.name + ".unpacked")
                if not marker.exists():
                    with tarfile.open(root / source.name) as archive:
                        archive.extractall(root / source.name.removesuffix(".tar.gz"), filter="data")
                    marker.touch()
    if args.noise:
        clips = snsd_files()
        print(f"MS-SNSD CC0 noise: {len(clips)} clips")
        if args.yes:
            for folder, name in clips:
                dest = root / "ms-snsd" / folder / name
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    _get(f"{_SNSD}/{folder}/{name}", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
