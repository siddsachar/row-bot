"""Getting ffmpeg (F3): PATH first; otherwise a pinned PyPI wheel, downloaded only after asking.

The wheel comes from a fake index on loopback through the one safe fetcher (a fake DNS answer
for files.pythonhosted.org carries the connection to a local server); nothing leaves this
computer. The wheel and the program inside it are checked against their fingerprints, and only
the program lands in the data folder. Asking goes through the approval gate by level: Ask asks,
Auto's reviewer may approve, Hands-off allows, Block refuses.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import zipfile
from types import SimpleNamespace

import pytest

from row_bot import ffmpeg_runtime
from tests.fixtures.public_web import PUBLIC_ADDRESS, public_web

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

BINARY = b"\x7fELF fake ffmpeg program " * 64
URL = "https://files.pythonhosted.org/packages/aa/bb/fake/imageio_ffmpeg-0.6.0-py3-none-test.whl"


def _wheel(binary: bytes = BINARY, member: str = "imageio_ffmpeg/binaries/ffmpeg-test-v7.1") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("imageio_ffmpeg/__init__.py", "from ._utils import get_ffmpeg_exe\n")
        archive.writestr(member, binary)
        archive.writestr("imageio_ffmpeg-0.6.0.dist-info/METADATA", "Name: imageio-ffmpeg\n")
    return buffer.getvalue()


def _pin(wheel: bytes, binary: bytes = BINARY, **changes) -> ffmpeg_runtime.Wheel:
    values = dict(
        key="test-x86_64", platform="Test", filename="imageio_ffmpeg-0.6.0-py3-none-test.whl", url=URL,
        size=len(wheel), sha256=hashlib.sha256(wheel).hexdigest(), member="imageio_ffmpeg/binaries/ffmpeg-test-v7.1",
        binary_size=len(binary), binary_sha256=hashlib.sha256(binary).hexdigest(), ffmpeg_version="7.1",
        build="test build", licence="GPL version 3 or later", build_source="https://ffmpeg.org/",
    )
    values.update(changes)
    return ffmpeg_runtime.Wheel(**values)


@pytest.fixture
def index(tmp_path, monkeypatch):
    """A fake PyPI file host on loopback, a fresh data folder and no ffmpeg on PATH."""
    import urllib.request

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(ffmpeg_runtime, "_VERIFIED", {})
    monkeypatch.setattr(ffmpeg_runtime, "_STATE", ffmpeg_runtime.DownloadState())
    with public_web(monkeypatch) as web:
        web.dns["files.pythonhosted.org"] = [PUBLIC_ADDRESS]
        state = SimpleNamespace(web=web, data=tmp_path / "data", pin=None)

        def serve(wheel: bytes, pin: ffmpeg_runtime.Wheel | None = None) -> ffmpeg_runtime.Wheel:
            state.pin = pin or _pin(wheel)
            web.file(URL, wheel)
            monkeypatch.setattr(ffmpeg_runtime, "current_wheel", lambda *_a, **_k: state.pin)
            return state.pin

        state.serve = serve
        yield state


def _files(folder) -> list[str]:
    return sorted(str(path.relative_to(folder)).replace("\\", "/") for path in folder.rglob("*") if path.is_file())


# ── Which ffmpeg ────────────────────────────────────────────────────────────


def _fake_version(monkeypatch, text: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(command, **_kwargs):
        calls.append(list(command))
        return SimpleNamespace(stdout=text, stderr="", returncode=0)

    monkeypatch.setattr(ffmpeg_runtime.subprocess, "run", run)
    return calls


def test_the_ffmpeg_on_path_is_used_first_with_its_version_and_licence(index, tmp_path, monkeypatch):
    pin = index.serve(_wheel())
    ffmpeg_runtime.install()  # Row-Bot's own copy is there too…
    folder = tmp_path / "tools"
    folder.mkdir()
    program = folder / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    program.write_bytes(b"program")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join(["relative-bin", str(folder)]))
    calls = _fake_version(monkeypatch, "ffmpeg version 6.1.1 Copyright\nconfiguration: --enable-gpl --enable-libx264\n")

    found = ffmpeg_runtime.find()

    assert (found.executable, found.source) == (str(program), "path")  # …but PATH comes first.
    assert (found.version, found.licence) == ("6.1.1", "GPL version 2 or later")
    assert found.describe() == "ffmpeg 6.1.1 (GPL version 2 or later), from this computer"
    assert calls == [[str(program), "-hide_banner", "-version"]]
    assert pin.key in str(ffmpeg_runtime.installed().executable)


def test_relative_path_entries_and_row_bot_s_data_folder_are_never_searched(index, tmp_path, monkeypatch):
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    planted = index.data / "bin"
    planted.mkdir(parents=True)
    (planted / name).write_bytes(b"planted")
    (planted / name).chmod(0o755)
    monkeypatch.chdir(tmp_path)
    (tmp_path / name).write_bytes(b"planted in the current folder")
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(planted)]))

    assert ffmpeg_runtime.find() is None


def test_licences_read_from_a_build_s_configuration():
    assert ffmpeg_runtime.licence_from_configuration("--enable-gpl --enable-version3") == "GPL version 3 or later"
    assert ffmpeg_runtime.licence_from_configuration("--enable-gpl") == "GPL version 2 or later"
    assert ffmpeg_runtime.licence_from_configuration("--enable-shared") == "LGPL version 2.1 or later"
    assert ffmpeg_runtime.licence_from_configuration("--enable-gpl --enable-nonfree") == "nonfree, not redistributable"


def test_the_offered_downloads_come_only_from_pypi_and_never_the_unredistributable_intel_mac_build():
    for wheel in ffmpeg_runtime.WHEELS.values():
        assert wheel.url.startswith("https://files.pythonhosted.org/packages/")
        assert wheel.url.endswith("/" + wheel.filename)
        assert wheel.licence.startswith("GPL version")
        assert len(wheel.sha256) == len(wheel.binary_sha256) == 64
    assert ffmpeg_runtime.current_wheel("Windows", "AMD64").key == "windows-x86_64"
    assert ffmpeg_runtime.current_wheel("Darwin", "arm64").key == "macos-aarch64"
    assert ffmpeg_runtime.current_wheel("Linux", "aarch64").key == "linux-aarch64"
    # The Intel Mac wheel's ffmpeg is built "nonfree, not redistributable": never offered.
    assert ffmpeg_runtime.current_wheel("Darwin", "x86_64") is None
    assert "brew install ffmpeg" in ffmpeg_runtime.unavailable_reason("Darwin", "x86_64")
    assert ffmpeg_runtime.current_wheel("Windows", "ARM64") is None
    assert "winget install Gyan.FFmpeg" in ffmpeg_runtime.unavailable_reason("Windows", "ARM64")


# ── Downloading ─────────────────────────────────────────────────────────────


def test_only_the_program_from_a_verified_wheel_lands_in_the_data_folder(index):
    pin = index.serve(_wheel())

    found = ffmpeg_runtime.install()

    folder = ffmpeg_runtime.install_folder(pin)
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    assert _files(index.data / "ffmpeg") == [f"{folder.name}/{name}", f"{folder.name}/install.json"]
    assert (folder / name).read_bytes() == BINARY
    assert (found.source, found.version, found.licence) == ("row_bot", "7.1", "GPL version 3 or later")
    assert found.describe() == "ffmpeg 7.1 (GPL version 3 or later), downloaded from PyPI"
    assert index.web.served == ["files.pythonhosted.org" + URL.split("files.pythonhosted.org", 1)[1]]
    if os.name != "nt":
        assert stat.S_IMODE((folder / name).stat().st_mode) == 0o755
    assert ffmpeg_runtime.download_state()["status"] == "installed"
    assert ffmpeg_runtime.find() == found
    # Asked again: nothing is downloaded twice.
    ffmpeg_runtime.install()
    assert len(index.web.served) == 1


def test_a_wheel_with_the_wrong_fingerprint_is_refused_and_nothing_is_unpacked(index):
    wheel = _wheel()
    index.serve(wheel, _pin(wheel, sha256="0" * 64))

    with pytest.raises(ffmpeg_runtime.FfmpegError, match="didn't match the fingerprint"):
        ffmpeg_runtime.install()

    assert not (index.data / "ffmpeg").exists() or _files(index.data / "ffmpeg") == []
    assert ffmpeg_runtime.find() is None
    assert ffmpeg_runtime.download_state()["status"] == "failed"


def test_a_wheel_whose_program_is_not_the_reviewed_one_is_refused(index):
    wheel = _wheel(b"something else entirely" * 10)
    index.serve(wheel, _pin(wheel))  # The wheel matches, the program inside doesn't.

    with pytest.raises(ffmpeg_runtime.FfmpegError):
        ffmpeg_runtime.install()

    assert _files(index.data / "ffmpeg") == []
    assert ffmpeg_runtime.find() is None


def test_a_larger_download_than_pinned_is_stopped(index):
    wheel = _wheel()
    index.serve(wheel + b"padding", _pin(wheel))

    with pytest.raises(ffmpeg_runtime.FfmpegError, match="larger than expected"):
        ffmpeg_runtime.install()
    assert ffmpeg_runtime.find() is None


def test_stopping_the_download_installs_nothing(index):
    index.serve(_wheel())

    def stop():
        raise ffmpeg_runtime.FfmpegStopped("Stopped.")

    with pytest.raises(ffmpeg_runtime.FfmpegStopped):
        ffmpeg_runtime.install(check=stop)
    assert ffmpeg_runtime.find() is None
    assert ffmpeg_runtime.download_state()["status"] == "stopped"


def test_a_changed_program_is_no_longer_used(index):
    pin = index.serve(_wheel())
    found = ffmpeg_runtime.install()
    with open(found.executable, "r+b") as program:
        program.write(b"X")
    ffmpeg_runtime._VERIFIED.clear()

    assert ffmpeg_runtime.installed(pin) is None


# ── Asking first ────────────────────────────────────────────────────────────


@pytest.fixture
def gate(monkeypatch):
    from row_bot.approval_review import ReviewVerdict
    from row_bot.tools import approval_gate

    state = SimpleNamespace(mode="approve", answer=True, asked=[], reviewed=[], verdict=ReviewVerdict(
        "approve", "You asked to record the screen, which needs it."))
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: state.mode)
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: state.asked.append(payload) or state.answer)
    monkeypatch.setattr("row_bot.approval_review.review",
                        lambda payload, **_kw: state.reviewed.append(payload) or state.verdict)
    return state


def test_ask_asks_with_the_size_source_and_licence_then_downloads(index, gate):
    index.serve(_wheel())

    found, line = ffmpeg_runtime.ensure("frames", "analyze_image")

    [asked] = gate.asked
    assert asked["question"] == "Looking at frames of a video needs ffmpeg (about 1 MB) from PyPI. Download it now?"
    assert "files.pythonhosted.org" in asked["description"] and "GPL version 3 or later" in asked["description"]
    assert asked["args"]["from"] == "files.pythonhosted.org"
    assert line == "Approval: asked; approved by you"
    assert found is not None and found.source == "row_bot"


def test_a_denied_download_downloads_nothing(index, gate):
    index.serve(_wheel())
    gate.answer = False

    found, refusal = ffmpeg_runtime.ensure("edit", "edit_video")

    assert found is None
    assert "nothing was downloaded" in refusal
    assert index.web.served == []


def test_block_refuses_without_asking_or_downloading(index, gate):
    index.serve(_wheel())
    gate.mode = "block"

    found, refusal = ffmpeg_runtime.ensure("edit", "edit_video")

    assert found is None and refusal.startswith("BLOCKED: Video editing needs ffmpeg")
    assert gate.asked == [] and index.web.served == []


def test_auto_s_reviewer_may_approve_and_otherwise_asks(index, gate):
    index.serve(_wheel())
    gate.mode = "auto"

    found, line = ffmpeg_runtime.ensure("screen", "computer_use")

    [reviewed] = gate.reviewed
    assert reviewed["label"] == "Download ffmpeg"
    assert gate.asked == []
    assert line.startswith("Approval: approved by the reviewer")
    assert found is not None


def test_auto_asks_the_person_when_the_reviewer_does(index, gate):
    from row_bot.approval_review import ReviewVerdict

    index.serve(_wheel())
    gate.mode = "auto"
    gate.verdict = ReviewVerdict("ask", "The person didn't ask for a video.")
    gate.answer = False

    found, refusal = ffmpeg_runtime.ensure("edit", "edit_video")

    [asked] = gate.asked
    assert asked["review"] == {"decision": "ask", "reason": "The person didn't ask for a video."}
    assert found is None and index.web.served == []


def test_hands_off_allows_and_records_it(index, gate):
    index.serve(_wheel())
    gate.mode = "full"

    found, line = ffmpeg_runtime.ensure("edit", "edit_video")

    assert gate.asked == [] and gate.reviewed == []
    assert line == "Approval: allowed by Hands-off"
    assert found is not None


def test_nothing_asks_when_ffmpeg_is_already_there(index, gate):
    index.serve(_wheel())
    ffmpeg_runtime.install()

    found, line = ffmpeg_runtime.ensure("edit", "edit_video")

    assert found is not None and line == "" and gate.asked == []
