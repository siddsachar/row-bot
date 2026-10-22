"""Where the release check may write, and when it may use the graphics card (S1).

- :func:`check_scratch` refuses a folder that is (or is inside) the everyday Row-Bot profile, its
  backups, the eval profile or a Row-Bot checkout: the seeded runs only ever write under the scratch
  folders they are given.
- :class:`GpuGate` books the shared graphics card in a booking file before GPU-heavy work (a line
  ``<time> <lane> START <what>`` and later ``<time> <lane> END <what>``), waits while another lane has an
  open booking (an ``EXCLUSIVE`` window included, O105) and says so once in a note, gives a lane whose
  latest line says it waits a few minutes to book first (O116), and waits while the person's own Row-Bot
  trains (its training engine runs from the everyday profile's ``runtimes`` folder): **the person's
  Row-Bot always wins**.
- :func:`quiet_reason`: the person's quiet hours (O116): no GPU work from 00:40 to 05:00 (their Row-Bot may
  train and compare from 01:00), and, when coming back after them, not while the card holds a large
  allocation that no lane booked (their comparison may still run).

Neither imports Row-Bot.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

#: How long the gate waits between looks while the card is taken.
WAIT_SECONDS = 60.0
#: How long a lane that wrote that it waits gets to book first (O116: the lanes yield between steps).
YIELD_SECONDS = 180.0
#: The person's quiet hours, for the seeded runs (O116): nothing new starts from QUIET_START, running work
#: stops at QUIET_STOP (their Row-Bot may train and compare from 01:00), nothing until QUIET_END.
QUIET_START = (0, 40)
QUIET_STOP = (0, 43)
QUIET_END = (5, 0)
AFTER_QUIET_UNTIL = (8, 0)
#: Coming back after the quiet hours: the card holding more than this, booked by no lane, means the
#: person's own Row-Bot (or something else) still uses it.
NON_LANE_MIB = 6144
#: A checkout's own ignored scratch folders, where test runs keep their temporary folders.
SCRATCH_IN_CHECKOUT = (".tmp", ".testtmp")
_LINE = re.compile(r"^\s*(\d{1,2}:\d{2})\s+(\S+)\s+(EXCLUSIVE\s+)?(START|END)\b\s*(.*)$")
_NOTE = re.compile(r"^\s*(\d{1,2}:\d{2})\s+(\S+)\s+note:\s*(.*)$")
#: A lane's exclusive window (O105): nobody else starts GPU work or heavy test lanes in it.
EXCLUSIVE = "EXCLUSIVE"


class ScratchError(RuntimeError):
    """A folder the release check must not use, or a run it can't start."""


def everyday_profile() -> Path:
    return (Path.home() / ".row-bot").resolve()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


#: More folders a seeded run must never write in (the everyday profile's backups, say), separated by
#: ``os.pathsep``.
PROTECT_ENV = "ROW_BOT_SEEDED_PROTECT"


def forbidden_roots(eval_profile: Path | None = None, extra: Iterable[Path] = ()) -> list[Path]:
    """Folders a seeded run never writes in."""

    roots = [everyday_profile()]
    roots.extend(Path(item) for item in os.environ.get(PROTECT_ENV, "").split(os.pathsep) if item.strip())
    if eval_profile is not None:
        roots.append(Path(eval_profile))
    roots.extend(extra)
    return roots


def check_scratch(folder: Path, eval_profile: Path | None = None, *, extra: Iterable[Path] = ()) -> Path:
    """``folder``, resolved, when a seeded run may write there; otherwise :class:`ScratchError`."""

    resolved = Path(folder).resolve()
    for root in forbidden_roots(eval_profile, extra):
        if _inside(resolved, root) or _inside(root, resolved):
            raise ScratchError(f"{resolved} is or holds {root}: the release check writes only to scratch folders")
    probe = resolved
    while probe != probe.parent:
        if (probe / ".git").exists() and (probe / "src" / "row_bot").is_dir():
            if resolved != probe and resolved.relative_to(probe).parts[0] in SCRATCH_IN_CHECKOUT:
                break  # the checkout's own ignored scratch folders (the test runs' temporary folders)
            raise ScratchError(f"{resolved} is inside a Row-Bot checkout ({probe}): use a scratch folder")
        probe = probe.parent
    return resolved


def open_bookings(text: str) -> dict[str, str]:
    """Each lane's open booking (a START with no END after it): lane -> what. A lane's exclusive
    window (``<time> <lane> EXCLUSIVE START``, closed by an ``EXCLUSIVE END``) is its own entry,
    ``<lane> EXCLUSIVE``, that the lane's ordinary bookings inside it don't close."""

    found: dict[str, str] = {}
    for line in text.splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        _, lane, exclusive, word, what = match.groups()
        what = what.strip()
        exclusive_line = bool(exclusive) or (word == "END" and what.upper().startswith(EXCLUSIVE))
        key = f"{lane} {EXCLUSIVE}" if exclusive_line else lane
        if word == "START":
            found[key] = what
        else:
            found.pop(key, None)
    return found


def exclusive_open(text: str) -> list[str]:
    """The lanes whose exclusive window is open now (O105: no GPU job, no ``pr``/``platform`` lane and
    no pytest with more than 2 workers meanwhile)."""

    return sorted(key.split(" ", 1)[0] for key in open_bookings(text) if key.endswith(f" {EXCLUSIVE}"))


def waiting_lanes(text: str) -> dict[str, str]:
    """Lanes whose latest line is a note saying they wait for the card (and that hold no booking)."""

    latest: dict[str, str | None] = {}
    for line in text.splitlines():
        booking = _LINE.match(line)
        if booking:
            latest[booking.group(2)] = None
            continue
        note = _NOTE.match(line)
        if note:
            words = note.group(3)
            latest[note.group(2)] = words if re.search(r"\bwait", words, re.IGNORECASE) else None
    return {lane: words for lane, words in latest.items() if words}


def _minutes(moment: tuple[int, int]) -> int:
    return moment[0] * 60 + moment[1]


def in_quiet_hours(now: datetime | None = None, *, start: tuple[int, int] = QUIET_START) -> bool:
    current = now or datetime.now()
    minute = current.hour * 60 + current.minute
    return _minutes(start) <= minute < _minutes(QUIET_END)


def card_memory_mib() -> int | None:
    """Graphics memory in use on the card now (nvidia-smi), or None when it can't be read."""

    import subprocess

    try:
        found = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=20)
        return int(found.stdout.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def after_quiet_hours(now: datetime | None = None) -> bool:
    """05:00 to 08:00: the person's Row-Bot may still be comparing what it trained overnight."""

    current = now or datetime.now()
    return _minutes(QUIET_END) <= current.hour * 60 + current.minute < _minutes(AFTER_QUIET_UNTIL)


def quiet_reason(now: datetime | None = None) -> str | None:
    """Why the person's quiet hours keep the card free now (None: they don't)."""

    if in_quiet_hours(now):
        return "the person's quiet hours (00:40-05:00: their Row-Bot may train and compare)"
    return None


def person_is_training(processes: Callable[[], Iterable[str]] | None = None,
                       profile: Path | None = None) -> bool:
    """Whether the person's own Row-Bot runs its training engine now (a process started from the
    everyday profile's ``runtimes`` folder)."""

    folder = str((profile or everyday_profile()) / "runtimes").lower()
    for path in (processes or _process_paths)():
        if path and path.lower().startswith(folder):
            return True
    return False


def _process_paths() -> Iterable[str]:
    try:
        import psutil
    except ImportError:
        return ()
    found = []
    for process in psutil.process_iter(["exe"]):
        try:
            found.append(str(process.info.get("exe") or ""))
        except Exception:  # noqa: BLE001 - a process that ended or that we may not read
            continue
    return found


class GpuGate:
    """Books the shared card for this lane (see the module's docstring). Without a booking file it only
    waits for the person's own training."""

    def __init__(self, booking: Path | None, lane: str, *, label: str = "S1",
                 clock: Callable[[], str] = lambda: time.strftime("%H:%M"),
                 sleep: Callable[[float], None] = time.sleep,
                 training: Callable[[], bool] = person_is_training,
                 quiet: Callable[[], str | None] = quiet_reason,
                 card: Callable[[], int | None] = card_memory_mib,
                 after_quiet: Callable[[], bool] = lambda: after_quiet_hours(),
                 say: Callable[[str], None] = lambda text: print(text, flush=True)) -> None:
        self.booking = booking
        self.lane = lane
        self.label = label
        self.clock = clock
        self.sleep = sleep
        self.training = training
        self.quiet = quiet
        self.card = card
        self.after_quiet = after_quiet
        self.say = say
        self.held: str | None = None
        self.waited = 0.0

    def _read(self) -> str:
        try:
            return self.booking.read_text(encoding="utf-8") if self.booking else ""
        except OSError:
            return ""

    def _append(self, line: str) -> None:
        if self.booking is None:
            return
        with self.booking.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line.rstrip("\n") + "\n")

    def acquire(self, what: str, expect: str = "") -> None:
        """Wait until the card is ours, then book it (once per holding: a second call is a no-op).
        ``expect`` says how long and how much graphics memory, as the booking file asks."""

        if self.held is not None:
            return
        said = ""
        noted = False
        yielded = 0.0
        while True:
            text = self._read()
            others = {lane: item for lane, item in open_bookings(text).items() if lane != self.lane}
            waiting_now = waiting_lanes(text)
            # A lane that wrote it waits books first, unless we wrote it first (then it yielded to us) or
            # still hold our booking (a person's process started again after a crash takes it over).
            ours = self.lane in waiting_now or self.lane in open_bookings(text)
            waiting = {} if ours else {lane: words for lane, words in waiting_now.items() if lane != self.lane}
            if self.training():
                why = f"GPU: the person's own Row-Bot is training; waiting ({self.label})"
            elif others:
                why = f"GPU: {', '.join(sorted(others))} booked it ({'; '.join(others.values())[:120]}); waiting"
                if not noted:  # O116: the other lane stops between its steps when we say we wait
                    self._append(f"{self.clock()} {self.lane} note: {self.label} waits for the card (next: {what}"
                                 + (f"; expect {expect}" if expect else "") + ")")
                    noted = True
            elif quiet := self.quiet():
                why = f"GPU: {quiet}; waiting"
            elif self.after_quiet() and (memory := self.card()) is not None and memory > NON_LANE_MIB:
                why = f"GPU: the card holds {memory:,} MiB that no lane booked (after the quiet hours); waiting"
            elif waiting and yielded < YIELD_SECONDS:
                why = f"GPU: {', '.join(sorted(waiting))} wrote that it waits; it books first"
                yielded += WAIT_SECONDS
            else:
                break
            if why != said:  # said once per reason, not once a minute
                self.say(why)
                said = why
            self.sleep(WAIT_SECONDS)
            self.waited += WAIT_SECONDS
        mine = open_bookings(self._read()).get(self.lane)
        if mine is None:
            terms = "; ".join(item for item in (f"expect {expect}" if expect else "",
                                                 "yields to the person's Learning engine") if item)
            self._append(f"{self.clock()} {self.lane} START {self.label}: {what} ({terms})")
        self.held = what

    def release(self) -> None:
        if self.held is None and self.lane not in open_bookings(self._read()):
            return
        if self.lane in open_bookings(self._read()):
            self._append(f"{self.clock()} {self.lane} END {self.label}")
        self.held = None


__all__ = ["EXCLUSIVE", "GpuGate", "PROTECT_ENV", "ScratchError", "card_memory_mib", "check_scratch",
           "after_quiet_hours", "everyday_profile", "exclusive_open", "forbidden_roots", "in_quiet_hours", "open_bookings",
           "person_is_training", "quiet_reason", "waiting_lanes"]
