"""A host that starts Row-Bot's container as root (Railway, ``RAILWAY_RUN_UID=0``) gets Row-Bot as uid 10001 (R1).

Before anything else, the launcher makes the data folder 10001's (all of it, only while its top isn't)
and drops root for good; then it reads secrets, writes the data folder and starts its server. Started
as 10001, or as root outside a container, nothing changes; when the drop fails, Row-Bot doesn't start.
Security-sensitive, so these stay in the pull-request lane. Root itself is a stand-in here (no test
runs as root); the image's own run as root is proven by the Railway-shape hosting smoke in
container.yml.
"""
from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import row_bot.launcher as launcher
from row_bot import container_user, environment_secrets

pytestmark = pytest.mark.platform

ROW_BOT = (10001, 10001)
SERVE = ["--server", "--no-open", "--no-splash", "--no-ollama", "--port", "8151"]


class StandInRoot:
    """What Row-Bot sees as root in a container. Each call is recorded and none reaches the system;
    ownership changes are remembered, so a second start sees what the first one did."""

    def __init__(self, events: list, data: Path, *, owners: dict | None = None, mounted: Path | None = None,
                 refuse_drop: bool = False, root_comes_back: bool = False, keeps_groups: bool = False) -> None:
        self.events, self.data, self.mounted = events, data, mounted
        self.owners: dict[Path, tuple[int, int]] = {} if owners is None else owners
        self.uid, self.gid, self.groups = 0, 0, [0, 27]
        self.refuse_drop, self.root_comes_back, self.keeps_groups = refuse_drop, root_comes_back, keeps_groups

    def geteuid(self) -> int:
        return self.uid

    def lstat(self, path):
        real = os.lstat(path)
        uid, gid = self.owners.get(Path(path), (0, 0))
        mounted = self.mounted is not None and Path(path).is_relative_to(self.mounted)
        return SimpleNamespace(st_mode=real.st_mode, st_uid=uid, st_gid=gid, st_dev=real.st_dev + mounted)

    def chown(self, path, uid: int, gid: int, *, follow_symlinks: bool = True) -> None:
        assert self.uid == 0 and follow_symlinks is False
        self.events.append(("chown", Path(path)))
        self.owners[Path(path)] = (uid, gid)

    def chmod(self, path, mode: int) -> None:
        self.events.append(("chmod", Path(path), mode))

    def setgroups(self, groups: list[int]) -> None:
        self._as_root()
        if not self.keeps_groups:
            self.groups = list(groups)
        self.events.append(("setgroups", tuple(groups)))

    def setresgid(self, real: int, effective: int, saved: int) -> None:
        self._as_root()
        self.gid = effective
        self.events.append(("setresgid", real, effective, saved))

    def setresuid(self, real: int, effective: int, saved: int) -> None:
        self._as_root()
        if self.refuse_drop:
            raise PermissionError(1, "Operation not permitted")
        # What Row-Bot had written by the time it stopped being root.
        written = sorted(p.name for p in self.data.iterdir() if p.name not in {"lost+found", "notes.txt"})
        self.uid = effective
        self.events.append(("setresuid", real, effective, saved, tuple(written)))

    def getresuid(self) -> tuple[int, int, int]:
        return (self.uid,) * 3

    def getresgid(self) -> tuple[int, int, int]:
        return (self.gid,) * 3

    def getgroups(self) -> list[int]:
        return self.groups

    def setuid(self, uid: int) -> None:
        if self.uid != 0 and not self.root_comes_back:
            raise PermissionError(1, "Operation not permitted")
        self.uid = uid
        self.events.append(("root again",))

    def _as_root(self) -> None:
        if self.uid != 0:
            raise PermissionError(1, "Operation not permitted")


@pytest.fixture
def volume(tmp_path, monkeypatch, reload_for_data_dir):
    """Railway's shape: the image's environment, and a volume at the data folder that root owns."""
    data = tmp_path / "data"
    (data / "lost+found").mkdir(parents=True)
    (data / "lost+found" / "#12").write_bytes(b"")
    (data / "notes.txt").write_text("kept", encoding="utf-8")
    run_secrets = tmp_path / "run-secrets"
    run_secrets.mkdir()
    for name in container_user._IMAGE_FOLDERS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UV_CACHE_DIR", str(data / "cache" / "uv"))
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "elsewhere" / "torch"))
    monkeypatch.setenv("ROW_BOT_DEPLOYMENT_MODE", "server")
    monkeypatch.setenv("ROW_BOT_CONTAINERIZED", "1")
    monkeypatch.setenv("ROW_BOT_SECRETS_DIR", str(run_secrets))
    monkeypatch.setenv("ROW_BOT_INSTALL_COUNT", "0")
    monkeypatch.delenv("ROW_BOT_SECRET_STORE_KEY", raising=False)
    monkeypatch.delenv("ROW_BOT_SETUP_CODE", raising=False)
    reload_for_data_dir(data, "row_bot.secret_store")
    monkeypatch.setattr(launcher, "_LAUNCH_FILE_LOGGING", True)
    monkeypatch.setattr(launcher, "_claim_instance", lambda: True)
    return data


def _start(monkeypatch, events: list, root: StandInRoot) -> None:
    """One ``row-bot serve`` start as root, up to where it would start its server."""
    monkeypatch.setattr(container_user, "_system", root)
    monkeypatch.setattr(environment_secrets, "keep_environment_private", lambda: events.append(("private",)))
    monkeypatch.setattr(launcher, "_run_direct", lambda args: events.append(("server starts",)))
    launcher.main(list(SERVE))


def test_root_in_the_container_hands_the_volume_over_once_and_becomes_row_bots_user_before_anything_else(
    volume, monkeypatch, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="row_bot.container_user")
    events: list = []
    root = StandInRoot(events, volume)

    _start(monkeypatch, events, root)

    changed = [event[1] for event in events if event[0] == "chown"]
    assert set(changed) == {
        volume / "lost+found", volume / "lost+found" / "#12", volume / "notes.txt", volume
    }
    assert changed[-1] == volume  # the top last: a start stopped half-way hands it over again
    drop = [event[0] for event in events if event[0] != "chown"]
    assert drop == ["chmod", "setgroups", "setresgid", "setresuid", "private", "server starts"]
    assert ("chmod", volume, 0o700) in events
    assert ("setgroups", ()) in events and ("setresgid", 10001, 10001, 10001) in events
    setresuid = next(event for event in events if event[0] == "setresuid")
    assert setresuid[1:4] == (10001, 10001, 10001)
    assert setresuid[4] == ()  # nothing written, no secret read, no program started while root
    assert (root.uid, root.gid, root.groups) == (10001, 10001, [])
    # Afterwards, as 10001: the secret store's own key, the setup code, the image's folders under /data.
    assert (volume / "secret-store-key").is_dir() and (volume / "mobile.db").is_file()
    assert (volume / "cache" / "uv").is_dir()
    assert not (volume.parent / "elsewhere").exists()
    assert "Row-Bot gave" in caplog.text and "runs as uid 10001" in caplog.text

    # The next start: the volume is Row-Bot's now, so nothing is handed over again.
    events.clear()
    caplog.clear()
    _start(monkeypatch, events, StandInRoot(events, volume, owners=root.owners))

    assert [event[0] for event in events] == [
        "setgroups", "setresgid", "setresuid", "private", "server starts"
    ]
    assert "Row-Bot gave" not in caplog.text and "Row-Bot runs as uid 10001" in caplog.text


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ({"refuse_drop": True}, "couldn't give"),
        ({"keeps_groups": True}, "couldn't become uid 10001"),
        ({"root_comes_back": True}, "could become root again"),
    ],
)
def test_root_that_cannot_be_dropped_for_good_stops_row_bot_before_it_reads_or_starts_anything(
    volume, monkeypatch, failure, reason
) -> None:
    events: list = []
    before = sorted(path.name for path in volume.iterdir())

    with pytest.raises(SystemExit) as stopped:
        _start(monkeypatch, events, StandInRoot(events, volume, **failure))

    assert str(stopped.value.code).startswith("Row-Bot didn't start: ")
    assert reason in str(stopped.value.code)
    assert ("private",) not in events and ("server starts",) not in events
    assert sorted(path.name for path in volume.iterdir()) == before


@pytest.mark.parametrize(
    ("uid", "environment"),
    [
        (10001, {"ROW_BOT_CONTAINERIZED": "1", "ROW_BOT_DEPLOYMENT_MODE": "server"}),  # the image's own user
        (0, {"ROW_BOT_DEPLOYMENT_MODE": "server"}),  # root outside a container: as before
        (0, {"ROW_BOT_CONTAINERIZED": "1", "ROW_BOT_DEPLOYMENT_MODE": "desktop"}),
    ],
)
def test_the_image_user_and_root_outside_the_container_change_nothing(tmp_path, monkeypatch, uid, environment) -> None:
    events: list = []
    root = StandInRoot(events, tmp_path)
    root.uid = uid
    monkeypatch.setattr(container_user, "_system", root)

    assert container_user.run_as_container_user(environment) is False
    assert events == [] and root.uid == uid


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link needs a privilege on Windows")
def test_a_link_is_changed_itself_and_nothing_on_another_file_system_is_touched(tmp_path, monkeypatch) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("not Row-Bot's", encoding="utf-8")
    data = tmp_path / "data"
    (data / "mounted" / "deep").mkdir(parents=True)
    (data / "link").symlink_to(outside)
    (data / "linked-folder").symlink_to(tmp_path, target_is_directory=True)
    events: list = []
    monkeypatch.setattr(container_user, "_system", StandInRoot(events, data, mounted=data / "mounted"))

    assert container_user.give_data_folder(data) is True

    changed = {event[1] for event in events if event[0] == "chown"}
    assert changed == {data / "link", data / "linked-folder", data}
    assert stat.S_ISLNK(os.lstat(data / "link").st_mode)
