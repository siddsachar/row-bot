"""The server's memory (H12): a container's limit, its highest use, and restarts for lack of memory.

A folder of fake cgroup files stands in for the container's: nothing reads the real /sys/fs/cgroup.
A restart is the next process's start (``started``) after the last one either shut down
(``stopped``) or was stopped by the kernel (it never got to).
"""

from __future__ import annotations

import pytest

from row_bot import server_memory

pytestmark = pytest.mark.subsystem

GB = 2**30


class Cgroup:
    def __init__(self, root) -> None:
        self.root = root

    def set(self, limit: str = "max", peak: int | None = None) -> None:
        (self.root / "memory.max").write_text(f"{limit}\n", encoding="ascii")
        if peak is not None:
            (self.root / "memory.peak").write_text(f"{peak}\n", encoding="ascii")


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(folder))
    # One look at the use per test, when it says so: the watcher never ticks here.
    monkeypatch.setattr(server_memory, "WATCH_SECONDS", 3600.0)
    yield folder
    server_memory.stopped()


@pytest.fixture
def cgroup(tmp_path, monkeypatch, data):
    root = tmp_path / "cgroup"
    root.mkdir()
    monkeypatch.setattr(server_memory, "CGROUP_ROOT", root)
    return Cgroup(root)


def restart(cgroup: Cgroup, *, limit: int = 2 * GB) -> None:
    """The next start, in a new cgroup: its high-water mark starts again."""
    cgroup.set(str(limit), peak=GB // 2)
    server_memory.started()


def use(cgroup: Cgroup, peak: int, *, limit: int = 2 * GB) -> None:
    """This run's use rises to *peak*, and the watcher takes a look."""
    cgroup.set(str(limit), peak=peak)
    server_memory.sample()


def test_the_limit_is_the_containers_and_none_without_one(cgroup, monkeypatch):
    assert server_memory.limit_bytes() is None  # No memory files: no cgroup of its own.
    cgroup.set("max")
    assert server_memory.limit_bytes() is None
    cgroup.set(str(2 * GB))
    assert server_memory.limit_bytes() == 2 * GB
    # cgroup v1 says "no limit" with a number near the largest it holds.
    (cgroup.root / "memory.max").unlink()
    (cgroup.root / "memory").mkdir()
    (cgroup.root / "memory" / "memory.limit_in_bytes").write_text("9223372036854771712\n", encoding="ascii")
    assert server_memory.limit_bytes() is None
    (cgroup.root / "memory" / "memory.limit_in_bytes").write_text(f"{GB}\n", encoding="ascii")
    assert server_memory.limit_bytes() == GB
    # Windows and macOS have no cgroup at all.
    monkeypatch.setattr(server_memory, "CGROUP_ROOT", None)
    assert server_memory.limit_bytes() is None


def test_the_desktop_has_no_limit_so_nothing_is_read_written_or_shown(data, monkeypatch):
    monkeypatch.setattr(server_memory, "CGROUP_ROOT", None)
    ticks: list[int] = []

    assert server_memory.started(tick=lambda: ticks.append(1)) is None
    server_memory.sample()
    server_memory.stopped()
    assert server_memory.notice() is None
    assert server_memory.dismiss() is False
    assert not (data / server_memory.STATE_FILE).exists() and ticks == []


def test_a_restart_after_use_came_near_the_limit_is_noticed_from_the_kept_high_water_mark(cgroup):
    restart(cgroup)
    use(cgroup, int(1.95 * GB))
    assert server_memory.notice() is None  # Still running.

    restart(cgroup)  # The kernel stopped the last run: it never shut down.

    found = server_memory.notice()
    assert found is not None
    assert (found["limit_bytes"], found["peak_bytes"]) == (2 * GB, int(1.95 * GB))


def test_a_clean_shutdown_or_use_far_from_the_limit_is_no_out_of_memory_restart(cgroup):
    restart(cgroup)
    use(cgroup, int(1.95 * GB))
    server_memory.stopped()
    restart(cgroup)
    assert server_memory.notice() is None

    use(cgroup, GB)
    restart(cgroup)  # Stopped by something else, half full.
    assert server_memory.notice() is None


def test_the_notice_shows_once_and_after_dismissal_only_another_such_restart_brings_it_back(cgroup):
    restart(cgroup)
    use(cgroup, int(1.95 * GB))
    restart(cgroup)
    first = server_memory.notice()
    assert first is not None
    server_memory.stopped()
    restart(cgroup)  # A clean restart doesn't make another one.
    assert server_memory.notice() == first

    assert server_memory.dismiss() is True
    assert server_memory.notice() is None
    assert server_memory.dismiss() is False
    server_memory.stopped()
    restart(cgroup)
    assert server_memory.notice() is None

    use(cgroup, int(1.95 * GB))
    restart(cgroup)
    assert server_memory.notice() is not None


def test_a_server_resized_since_puts_the_notice_away(cgroup):
    restart(cgroup)
    use(cgroup, int(1.95 * GB))
    restart(cgroup, limit=4 * GB)
    assert server_memory.notice() is None


@pytest.mark.parametrize(("size", "words"), [
    (2 * GB, "2 GB"), (2_000_000_000, "2 GB"), (3 * GB // 2, "1.5 GB"), (4 * GB, "4 GB"), (GB // 4, "0.5 GB"),
])
def test_sizes_are_said_as_people_say_them(size, words):
    assert server_memory.size_words(size) == words
