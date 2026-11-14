"""The shared booking file for the graphics card and the ChatGPT eval profile.

Lanes share one append-only text file (``ROW_BOT_BENCH_BOOKING_FILE``), one line per event,
LF endings:

    HH:MM lane-x START <what> (expect <min> min, <GB> GB)
    HH:MM lane-x END <what>
    HH:MM lane-x EXCLUSIVE START <what>       (the whole card: nobody else runs anything)
    HH:MM lane-x note: ...

A lane's booking is open from its START to its next END. Bookings on the ChatGPT eval
profile name it (``ChatGPT eval-profile``); everything else is the graphics card. The
benchmark lane waits while another lane holds what it needs (or holds the card
exclusively), books it, and ends the booking when its task is done. New graphics-card work
does not start in the night and morning hours other lanes and the owner keep
(:data:`GPU_QUIET`).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import time as clock
from pathlib import Path
from typing import Callable

LANE = "lane-bench"
GPU = "gpu"
EVAL_PROFILE = "eval-profile"
EVAL_PROFILE_TAG = "ChatGPT eval-profile"
#: No new graphics-card work from 00:25 to 08:00 (another lane's night window, then the owner's Row-Bot).
GPU_QUIET = (clock(0, 25), clock(8, 0))
_LINE = re.compile(r"^\s*(\d{1,2}:\d{2})\s+(\S+)\s+(EXCLUSIVE\s+)?(START|END)\b\s*(.*)$")


@dataclass(frozen=True)
class Booking:
    lane: str
    kind: str
    exclusive: bool
    what: str
    at: str


def kind_of(what: str) -> str:
    return EVAL_PROFILE if EVAL_PROFILE_TAG.lower() in str(what or "").lower() else GPU


def open_bookings(text: str) -> list[Booking]:
    """The bookings still open in the file's text (notes and unknown lines are ignored)."""
    held: dict[tuple[str, str], Booking] = {}
    for line in text.splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        at, lane, exclusive, event, what = match.groups()
        kind = kind_of(what)
        if event == "START":
            held[(lane, kind)] = Booking(lane, kind, bool(exclusive), what.strip(), at)
            continue
        # An END closes the lane's booking of the kind it names; one that names nothing (or a
        # kind the lane has not booked) closes its card booking, else its eval-profile one.
        key = (lane, kind) if what.strip() and (lane, kind) in held else next(
            (candidate for candidate in ((lane, GPU), (lane, EVAL_PROFILE)) if candidate in held), None)
        if key is not None:
            held.pop(key)
    return list(held.values())


class BookingFile:
    def __init__(self, path: Path, *, lane: str = LANE, now: Callable[[], datetime] = datetime.now) -> None:
        self.path = Path(path)
        self.lane = lane
        self.now = now

    def _text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def blockers(self, kind: str) -> list[Booking]:
        """Other lanes' open bookings that keep this lane from *kind* now."""
        return [item for item in open_bookings(self._text())
                if item.lane != self.lane and (item.kind == kind or item.exclusive)]

    def quiet_hours(self, kind: str) -> bool:
        if kind != GPU:
            return False
        moment = self.now().time()
        return GPU_QUIET[0] <= moment < GPU_QUIET[1]

    def _write(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:  # binary: LF endings on every platform
            handle.write((f"{self.now().strftime('%H:%M')} {self.lane} {line}".rstrip() + "\n").encode("utf-8"))

    def start(self, kind: str, what: str, expect: str) -> None:
        label = f"{EVAL_PROFILE_TAG} {what}" if kind == EVAL_PROFILE else what
        self._write(f"START {label} (expect {expect})")

    def end(self, kind: str, what: str) -> None:
        self._write(f"END {EVAL_PROFILE_TAG} {what}" if kind == EVAL_PROFILE else f"END {what}")

    def wait_for(self, kind: str, *, stop: Callable[[], bool], poll_s: float = 60.0,
                 sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> bool:
        """Wait until nobody else holds *kind* (and, for the card, outside its quiet hours).
        False when the stop file appeared first."""
        said = ""
        while True:
            if stop():
                return False
            blockers = self.blockers(kind)
            if not blockers and not self.quiet_hours(kind):
                return True
            reason = (f"waiting: {blockers[0].lane} holds {blockers[0].kind} since {blockers[0].at}" if blockers
                      else "waiting: the graphics card's quiet hours (00:25-08:00)")
            if reason != said:
                log(reason)
                said = reason
            sleep(poll_s)
